"""The shared streaming reply path and the tool definitions it feeds the model.

Extracted from the former single-file bot.py.
"""

import asyncio
import json
import logging
import time

import aiosqlite
from telegram import Update

from app.core.config import settings
from app.telegram.i18n import t
from app.dependencies.provider_factory import get_provider
from app.schemas.chat import ChatCompletionRequest, ChatMessage

from .account import _linked_profile_id
from .conversations import _persist_exchange
from .state import (
    _action_messages,
    _chat_service,
    _locale,
    _memory_prefs,
    _rag_prefs,
    _split,
    _tools_prefs,
    counters,
)
from .ui import _QUICK_ACTIONS

logger = logging.getLogger(__name__)

# ── Shared streaming helper ─────────────────────────────────────────────────

async def _assemble_tool_defs(profile_id: str | None, force_refresh: bool = False) -> list[dict]:
    """Build the tool-definition list for the Telegram tool loop (Phase 23.b).

    Mirrors the web ``GET /v1/tools`` endpoint: built-in tools + every tool
    discovered from enabled MCP servers + the linked profile's custom tools.
    MCP discovery is cached in ``mcp_service``; we only re-probe when asked to
    (``/tools`` listing) or when the cache is cold, so ordinary messages don't
    pay the probe latency on every turn. Failures never hide the built-ins.
    """
    from app.services import custom_tool_service, mcp_service
    from app.tools.registry import TOOL_DEFINITIONS

    defs = list(TOOL_DEFINITIONS)
    try:
        if force_refresh or not mcp_service.get_tool_definitions():
            async with aiosqlite.connect(settings.db_path) as db:
                db.row_factory = aiosqlite.Row
                await mcp_service.refresh(db)
        defs.extend(mcp_service.get_tool_definitions())
    except Exception:  # noqa: BLE001 — MCP is optional; never break the tool list
        logger.exception("_assemble_tool_defs: MCP discovery failed")
    if profile_id:
        try:
            async with aiosqlite.connect(settings.db_path) as db:
                db.row_factory = aiosqlite.Row
                defs.extend(await custom_tool_service.get_tool_definitions(db, profile_id))
        except Exception:  # noqa: BLE001 — custom tools must never hide the built-ins
            logger.exception("_assemble_tool_defs: custom tool listing failed")
    return defs


async def _stream_reply(
    chat_id: int,
    session: list[dict],
    model: str,
    sent,  # the placeholder Message we edit
    update: Update | None,
    orig_message=None,
    persist_user: str | list | None = None,
) -> None:
    """Stream a provider response, edit *sent* as tokens arrive, attach quick-action buttons.

    When ``persist_user`` is set (a genuine new user turn) and streaming succeeds, the
    exchange is stored into the linked profile's active conversation (Phase 23.a).
    Quick-action refinements pass None to stay in-memory only."""

    # Bind a request id so Telegram-originated provider/sidecar logs correlate.
    from app.core.logging_context import set_request_id
    set_request_id()

    provider = get_provider(model)
    messages = [ChatMessage(role=m["role"], content=m["content"]) for m in session]

    # Phase 19: inject the linked profile's memory block (per-chat /memory toggle)
    linked_profile: str | None = None
    if _memory_prefs.get(chat_id, True) and settings.memory_enabled:
        try:
            linked_profile = await _linked_profile_id(chat_id)
            if linked_profile:
                from app.services import memory_service
                async with aiosqlite.connect(settings.db_path) as db:
                    db.row_factory = aiosqlite.Row
                    block = await memory_service.load_memory_block(db, linked_profile)
                if block:
                    if messages and messages[0].role == "system":
                        messages[0] = messages[0].model_copy(
                            update={"content": f"{messages[0].content}\n\n{block}"}
                        )
                    else:
                        messages.insert(0, ChatMessage(role="system", content=block))
        except Exception:
            logger.exception("stream_reply: iniezione memoria fallita chat_id=%s", chat_id)

    # Phase 21: retrieve and inject the linked profile's knowledge base (/rag toggle).
    # Mirrors ChatService._apply_rag — context folded into the last user message.
    rag_sources: list = []
    if _rag_prefs.get(chat_id, False):
        try:
            rag_profile = linked_profile or await _linked_profile_id(chat_id)
            if rag_profile:
                last_idx = next(
                    (i for i in range(len(messages) - 1, -1, -1)
                     if messages[i].role == "user" and isinstance(messages[i].content, str)),
                    None,
                )
                query = messages[last_idx].content if last_idx is not None else ""
                if query.strip():
                    from app.services import rag_service
                    async with aiosqlite.connect(settings.db_path) as db:
                        db.row_factory = aiosqlite.Row
                        await db.execute("PRAGMA foreign_keys=ON")
                        rag_sources = await rag_service.retrieve(db, rag_profile, query)
                    if rag_sources:
                        context = rag_service.build_context_block(rag_sources)
                        messages[last_idx] = messages[last_idx].model_copy(
                            update={"content": f"{context}\n\n---\n\nDomanda dell'utente:\n{query}"}
                        )
                        logger.info(
                            "stream_reply: RAG iniettate %d fonti chat_id=%s profile=%s",
                            len(rag_sources), chat_id, rag_profile,
                        )
        except Exception:
            logger.exception("stream_reply: iniezione RAG fallita chat_id=%s", chat_id)

    # Phase 23.b: when tools are enabled for this chat (and we're not in agent
    # mode — agent/* models orchestrate their own tools), merge the built-in +
    # custom + MCP tools into the request and run the shared server-side tool
    # loop instead of a plain stream. tool-call progress is surfaced live below.
    tools_active = _tools_prefs.get(chat_id, False) and not model.startswith("agent/")
    request = ChatCompletionRequest(model=model, messages=messages, max_tokens=2048)
    if tools_active:
        tool_profile = linked_profile or await _linked_profile_id(chat_id)
        tool_defs = await _assemble_tool_defs(tool_profile)
        if tool_defs:
            request = ChatCompletionRequest(
                model=model,
                messages=messages,
                max_tokens=2048,
                tools=tool_defs,
                profile_id=tool_profile,
            )
        else:
            tools_active = False

    def _chunk_source():
        """Yield native provider-style chunks whether or not tools are active.

        With tools active we adapt the web tool loop's SSE frames into the same
        shape the streaming consumer below already understands (tool_call /
        tool_result control chunks, content deltas, a terminating meta chunk)."""
        if not tools_active:
            return provider.stream(request)

        async def _adapt():
            async for frame in _chat_service._stream_with_tools(provider, request):
                event = frame.get("event")
                if event == "done":
                    return
                try:
                    payload = json.loads(frame.get("data") or "null")
                except (TypeError, ValueError):
                    continue
                if event == "tool_call":
                    yield {"_sse_event": "tool_call", "_icon": "⚙", "name": payload.get("name")}
                elif event == "tool_result":
                    yield {"_sse_event": "tool_result"}
                elif event == "error":
                    raise RuntimeError(payload.get("message") or "tool loop error")
                elif event == "message" and isinstance(payload, dict):
                    yield payload

        return _adapt()

    full_content = ""
    progress: list[str] = []
    last_edit = time.monotonic()

    try:
        async for chunk in _chunk_source():
            if chunk.get("object") == "chat.completion.meta":
                break

            sse_event = chunk.get("_sse_event")
            if sse_event == "tool_call":
                progress.append(f"{chunk.get('_icon', '🔧')} {chunk.get('name', 'agent')} …")
                now = time.monotonic()
                if now - last_edit >= 1.0:
                    try:
                        await sent.edit_text("\n".join(progress))
                        last_edit = now
                    except Exception:
                        pass
                continue
            if sse_event == "tool_result":
                if progress:
                    # flip the running-icon prefix (🔧 agent, ⚙ tool loop) to ✅
                    progress[-1] = (
                        progress[-1].replace("🔧", "✅").replace("⚙", "✅").removesuffix(" …")
                    )
                continue

            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = (choices[0].get("delta") or {}).get("content") or ""
            if not delta:
                continue

            full_content += delta
            now = time.monotonic()
            if now - last_edit >= 1.0:
                try:
                    await sent.edit_text(full_content + " ▌")
                    last_edit = now
                except Exception:
                    pass

        # Phase 21: append a 📚 sources footer when RAG grounded the reply.
        display_content = full_content
        if full_content and rag_sources:
            seen: list[str] = []
            for s in rag_sources:
                if s.filename not in seen:
                    seen.append(s.filename)
            display_content += t(_locale(chat_id), "rag_sources_header", sources=", ".join(seen))

        chunks = _split(display_content or "⚠ Nessuna risposta.")
        if len(chunks) == 1:
            await sent.edit_text(chunks[0], reply_markup=_QUICK_ACTIONS)
            _action_messages[sent.message_id] = chat_id
        else:
            await sent.edit_text(chunks[0])
            for extra in chunks[1:-1]:
                reply_target = update.message if update and update.message else orig_message
                if reply_target:
                    await reply_target.reply_text(extra)
            last_msg = None
            reply_target = update.message if update and update.message else orig_message
            if reply_target:
                last_msg = await reply_target.reply_text(chunks[-1], reply_markup=_QUICK_ACTIONS)
            if last_msg:
                _action_messages[last_msg.message_id] = chat_id

        if full_content:
            session.append({"role": "assistant", "content": full_content})
            counters.sent += 1
            logger.info("stream_reply: completata chat_id=%s model=%s len=%d", chat_id, model, len(full_content))

            # Phase 23.a: persist a genuine new turn into the linked profile's
            # active conversation (no-op for unlinked chats / quick-action refinements).
            if persist_user is not None:
                await _persist_exchange(chat_id, model, persist_user, full_content)

            # Phase 19: async memory extraction on the linked profile
            if linked_profile and _memory_prefs.get(chat_id, True):
                try:
                    from app.services import memory_service
                    last_user = next(
                        (m["content"] for m in reversed(session[:-1])
                         if m["role"] == "user" and isinstance(m["content"], str)), "")
                    if last_user:
                        memory_service.schedule_extraction(
                            linked_profile,
                            [ChatMessage(role="user", content=last_user),
                             ChatMessage(role="assistant", content=full_content)],
                        )
                except Exception:
                    logger.exception("stream_reply: estrazione memoria fallita chat_id=%s", chat_id)
        else:
            logger.warning("stream_reply: risposta vuota chat_id=%s model=%s", chat_id, model)

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        counters.errors += 1
        logger.exception("stream_reply: errore chat_id=%s model=%s", chat_id, model)
        try:
            await sent.edit_text(f"⚠ Errore: {exc}")
        except Exception:
            pass
        if session and session[-1]["role"] == "user":
            session.pop()
