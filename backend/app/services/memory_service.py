"""
memory_service — Phase 19 per-profile persistent memory.

Two halves:
  * Injection — compact the profile's enabled memories into a <user_memory>
    block appended to the system prompt (char budget, most-recent first).
  * Extraction — after each exchange an async low-cost LLM call receives the
    latest messages + existing memories and returns add/update/delete/noop
    operations, applied to profile_memories (dedup + consolidation).

Extraction failures are logged and swallowed: memory must never break chat.
"""

import asyncio
import json
import logging
import re

import aiosqlite

from app.core.config import settings
from app.db import memory_repository
from app.schemas.chat import ChatCompletionRequest, ChatMessage
from app.schemas.memories import MemoryOut

logger = logging.getLogger(__name__)

_EXTRACTION_SYSTEM_PROMPT = """\
You maintain a long-term memory of facts about a user, extracted from chat conversations.
You receive the user's EXISTING MEMORIES and the LATEST EXCHANGE.
Decide which durable facts about the user are worth remembering (preferences, personal facts,
ongoing projects, standing instructions). Ignore one-off requests, small talk, and anything
already covered by an existing memory.

Reply with ONLY a JSON array of operations (no prose, no markdown fence). Allowed operations:
  {"op": "add", "content": "<short fact, max 200 chars>", "category": "preference|fact|project|instruction"}
  {"op": "update", "id": "<existing memory id>", "content": "<revised fact>"}
  {"op": "delete", "id": "<existing memory id>"}
Return [] when there is nothing worth remembering. Write memories in the user's language.
Never store secrets, passwords or API keys."""


def _extraction_model() -> str:
    return settings.memory_extraction_model or settings.default_model


# ── Injection ────────────────────────────────────────────────────────────────

def build_memory_block(memories: list[MemoryOut], max_chars: int | None = None) -> str:
    """Compact enabled memories into a <user_memory> block within the char budget."""
    budget = max_chars or settings.memory_max_chars
    lines: list[str] = []
    used = 0
    for mem in memories:  # repository returns most-recent first
        line = f"- [{mem.category}] {mem.content}"
        if used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    return (
        "<user_memory>\n"
        "Things you remember about this user from previous conversations:\n"
        + "\n".join(lines)
        + "\n</user_memory>"
    )


async def load_memory_block(db: aiosqlite.Connection, profile_id: str) -> str:
    """Return the injectable memory block for a profile ('' when disabled/empty)."""
    if not settings.memory_enabled:
        return ""
    if not await memory_repository.get_memory_enabled(db, profile_id):
        return ""
    memories = await memory_repository.list_memories(db, profile_id, enabled_only=True)
    return build_memory_block(memories)


def apply_memory_block(request: ChatCompletionRequest, block: str) -> ChatCompletionRequest:
    """Append the memory block to the leading system message (or prepend one)."""
    if not block:
        return request
    messages = list(request.messages)
    if messages and messages[0].role == "system" and isinstance(messages[0].content, str):
        merged = f"{messages[0].content}\n\n{block}"
        messages[0] = messages[0].model_copy(update={"content": merged})
    else:
        messages.insert(0, ChatMessage(role="system", content=block))
    return request.model_copy(update={"messages": messages})


# ── Extraction ───────────────────────────────────────────────────────────────

def _parse_operations(raw: str) -> list[dict]:
    """Parse the model reply into a list of op dicts; tolerate markdown fences."""
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        ops = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [op for op in ops if isinstance(op, dict) and op.get("op")]


async def _apply_operations(
    db: aiosqlite.Connection,
    profile_id: str,
    ops: list[dict],
    conversation_id: str | None,
    existing: list[MemoryOut],
) -> int:
    existing_ids = {m.id for m in existing}
    existing_contents = {m.content.strip().lower() for m in existing}
    applied = 0
    for op in ops[:10]:  # sanity cap per exchange
        kind = op.get("op")
        if kind == "add":
            content = (op.get("content") or "").strip()[:500]
            if not content or content.lower() in existing_contents:
                continue
            if await memory_repository.count_memories(db, profile_id) >= settings.memory_max_items:
                logger.info("Memory: profile %s at max items, skipping add", profile_id)
                continue
            await memory_repository.create_memory(
                db, profile_id, content,
                category=op.get("category") or "fact",
                source_conversation_id=conversation_id,
            )
            existing_contents.add(content.lower())
            applied += 1
        elif kind == "update" and op.get("id") in existing_ids:
            content = (op.get("content") or "").strip()[:500]
            if not content:
                continue
            await memory_repository.update_memory(db, op["id"], content=content)
            applied += 1
        elif kind == "delete" and op.get("id") in existing_ids:
            await memory_repository.delete_memory(db, op["id"])
            applied += 1
    return applied


async def extract_from_exchange(
    profile_id: str,
    messages: list[ChatMessage],
    conversation_id: str | None = None,
) -> None:
    """Run the extraction LLM call for one exchange and apply the operations.

    Opens its own DB connection: callers fire this as a background task after
    the request's connection is gone.
    """
    # Only user/assistant text turns matter for extraction.
    exchange = [
        f"{m.role.upper()}: {m.content}"
        for m in messages
        if m.role in ("user", "assistant") and isinstance(m.content, str) and m.content.strip()
    ]
    if not exchange:
        return

    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    try:
        if not await memory_repository.get_memory_enabled(db, profile_id):
            return
        existing = await memory_repository.list_memories(db, profile_id)
        existing_block = "\n".join(
            f'{{"id": "{m.id}", "category": "{m.category}", "content": {json.dumps(m.content)}}}'
            for m in existing
        ) or "(none)"

        prompt = (
            f"EXISTING MEMORIES:\n{existing_block}\n\n"
            f"LATEST EXCHANGE:\n" + "\n".join(exchange)[-6000:]
        )

        from app.services.provider_factory import ProviderFactory

        model = _extraction_model()
        request = ChatCompletionRequest(
            model=model,
            messages=[
                ChatMessage(role="system", content=_EXTRACTION_SYSTEM_PROMPT),
                ChatMessage(role="user", content=prompt),
            ],
            temperature=0.0,
            max_tokens=512,
        )
        provider = ProviderFactory.get_provider(model)
        response = await provider.complete(request)
        choices = response.get("choices") or []
        content = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
        ops = _parse_operations(content)
        if not ops:
            logger.debug("Memory extraction: no operations for profile=%s", profile_id)
            return
        applied = await _apply_operations(db, profile_id, ops, conversation_id, existing)
        logger.info(
            "Memory extraction: profile=%s model=%s ops=%d applied=%d",
            profile_id, model, len(ops), applied,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — extraction must never surface to the user
        logger.exception("Memory extraction failed for profile=%s", profile_id)
    finally:
        await db.close()


def schedule_extraction(
    profile_id: str,
    messages: list[ChatMessage],
    conversation_id: str | None = None,
) -> None:
    """Fire-and-forget extraction task (no-op when the feature is off)."""
    if not settings.memory_enabled:
        return
    task = asyncio.get_event_loop().create_task(
        extract_from_exchange(profile_id, messages, conversation_id)
    )
    # Keep a reference so the task isn't garbage-collected mid-flight.
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


_background_tasks: set = set()
