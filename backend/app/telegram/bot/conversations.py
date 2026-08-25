"""Persisted conversations (Phase 23.a): hydration and exchange persistence.

Extracted from the former single-file bot.py.
"""

import logging

import aiosqlite

from app.core.config import settings
from app.db import telegram_prefs_repository as prefs_repo
from app.db import conversation_repository as conv_repo
from app.schemas.chat import ChatMessage

from .account import _linked_profile_id
from .state import _MAX_HISTORY, _active_convs, _hydrated, _sessions

logger = logging.getLogger(__name__)

# ── Persisted conversations (Phase 23.a) ──────────────────────────────────────
# For linked chats (/link), Telegram exchanges are stored as regular profile
# conversations (channel='telegram') so they show up in the web sidebar and share
# history across channels. Unlinked chats keep the in-memory _sessions buffer.

def _title_from(text: str, limit: int = 60) -> str:
    """Derive a provisional conversation title from the first user message."""
    title = " ".join((text or "").split())
    return (title[:limit] + "…") if len(title) > limit else (title or "Telegram")


def _user_text_of(content) -> str:
    """Flatten a (possibly multimodal) user message to plain text for persistence."""
    if isinstance(content, list):
        return next(
            (p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text"),
            "[media]",
        )
    return content if isinstance(content, str) else "[media]"


async def _ensure_hydrated(chat_id: int) -> None:
    """Load the active persisted conversation's recent messages into the in-memory
    working buffer once per process, so context survives a bot restart."""
    if chat_id in _hydrated:
        return
    _hydrated.add(chat_id)
    conv_id = _active_convs.get(chat_id)
    if not conv_id:
        return
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            conv = await conv_repo.get_conversation(db, conv_id)
        if conv and conv.messages:
            _sessions[chat_id] = [
                {"role": m.role, "content": m.content}
                for m in conv.messages
                if m.role in ("user", "assistant") and isinstance(m.content, str)
            ][-_MAX_HISTORY:]
        elif not conv:
            # Stale pointer (conversation deleted from the web) — drop it.
            _active_convs.pop(chat_id, None)
            await prefs_repo.set_active_conversation(chat_id, None)
    except Exception:
        logger.exception("hydrate: fallita chat_id=%s conv=%s", chat_id, conv_id)


async def _persist_exchange(chat_id: int, model: str, user_content, assistant_content: str) -> None:
    """Persist a completed user/assistant exchange for a linked chat, creating the
    active conversation on first use. No-op for unlinked chats (in-memory only)."""
    profile_id = await _linked_profile_id(chat_id)
    if not profile_id:
        return
    user_text = _user_text_of(user_content)
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            conv_id = _active_convs.get(chat_id)
            if not conv_id:
                summary = await conv_repo.create_conversation(
                    db, _title_from(user_text), model, profile_id, channel="telegram"
                )
                conv_id = summary.id
                _active_convs[chat_id] = conv_id
                _hydrated.add(chat_id)
                await prefs_repo.set_active_conversation(chat_id, conv_id)
                # Generate a nicer title asynchronously (only for a new conversation).
                from app.services import title_service
                title_service.schedule_titling(conv_id, user_text, assistant_content)
            await conv_repo.append_messages(
                db,
                conv_id,
                [
                    ChatMessage(role="user", content=user_text),
                    ChatMessage(role="assistant", content=assistant_content, model=model),
                ],
            )
    except Exception:
        logger.exception("persist_exchange: fallita chat_id=%s", chat_id)


async def _reset_conversation(chat_id: int) -> None:
    """Start a fresh conversation (/new): clear the working buffer and detach the
    active conversation so a linked chat lazily creates a new one on next message."""
    _sessions[chat_id].clear()
    _hydrated.add(chat_id)
    if _active_convs.pop(chat_id, None) is not None:
        await prefs_repo.set_active_conversation(chat_id, None)
