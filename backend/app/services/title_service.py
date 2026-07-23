"""
title_service — Phase 19 LLM auto-titling.

Generates a concise conversation title from the first exchange, replacing the
first-60-chars-of-the-first-message heuristic. Runs as a fire-and-forget task
after the first exchange is persisted; failures are logged and swallowed so
the heuristic title simply survives.
"""

import asyncio
import logging

import aiosqlite

from app.core.config import settings
from app.db import pool
from app.schemas.chat import ChatCompletionRequest, ChatMessage

logger = logging.getLogger(__name__)

_TITLE_PROMPT = (
    "Generate a short title (max 6 words, no quotes, no trailing punctuation) "
    "summarizing this conversation. Reply with the title only, in the "
    "conversation's language."
)

_background_tasks: set = set()


def _title_model() -> str:
    return settings.title_model or settings.memory_extraction_model or settings.default_model


async def generate_title(conversation_id: str, user_text: str, assistant_text: str) -> None:
    """LLM call + DB update. Opens its own connection (background task)."""
    from app.services.provider_factory import ProviderFactory

    model = _title_model()
    excerpt = f"USER: {user_text[:1000]}\n\nASSISTANT: {assistant_text[:1000]}"
    request = ChatCompletionRequest(
        model=model,
        messages=[
            ChatMessage(role="system", content=_TITLE_PROMPT),
            ChatMessage(role="user", content=excerpt),
        ],
        temperature=0.2,
        max_tokens=32,
    )
    try:
        provider = ProviderFactory.get_provider(model)
        response = await provider.complete(request)
        choices = response.get("choices") or []
        title = ((choices[0].get("message") or {}).get("content") or "").strip() if choices else ""
        title = title.strip('"“”\' \n').splitlines()[0][:80] if title else ""
        if not title:
            return
        db = await pool.checkout()
        try:
            from app.db import conversation_repository
            db.row_factory = aiosqlite.Row
            await conversation_repository.update_title(db, conversation_id, title)
        finally:
            await db.close()
        logger.info("Auto-title: conversation=%s model=%s title=%r", conversation_id, model, title)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — titling must never surface to the user
        logger.exception("Auto-title failed for conversation=%s", conversation_id)


def schedule_titling(conversation_id: str, user_text: str, assistant_text: str) -> None:
    """Fire-and-forget titling task (no-op when disabled or texts are empty)."""
    if not settings.auto_title_enabled or not user_text or not assistant_text:
        return
    task = asyncio.get_event_loop().create_task(
        generate_title(conversation_id, user_text, assistant_text)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
