"""
telegram_prefs_repository — per-chat Telegram preferences (UI locale).

The bot keeps an in-memory cache of chat_id → locale (loaded at startup and
updated on /lang) so handlers resolve the locale synchronously without a DB
round-trip on every message.  This module owns persistence; the cache lives in
the bot module.
"""

import logging
import time

import aiosqlite

from app.core.config import settings

logger = logging.getLogger(__name__)


async def _connect() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    return db


async def load_all() -> dict[int, str]:
    """Return all persisted chat_id → locale mappings (for warm-start caching)."""
    db = await _connect()
    try:
        async with db.execute("SELECT chat_id, locale FROM telegram_prefs") as cursor:
            rows = await cursor.fetchall()
        return {row["chat_id"]: row["locale"] for row in rows}
    finally:
        await db.close()


async def load_all_memory() -> dict[int, bool]:
    """Return all persisted chat_id → memory-toggle mappings (Phase 19)."""
    db = await _connect()
    try:
        async with db.execute("SELECT chat_id, memory FROM telegram_prefs") as cursor:
            rows = await cursor.fetchall()
        return {row["chat_id"]: bool(row["memory"]) for row in rows}
    finally:
        await db.close()


async def load_all_rag() -> dict[int, bool]:
    """Return all persisted chat_id → RAG-toggle mappings (Phase 21)."""
    db = await _connect()
    try:
        async with db.execute("SELECT chat_id, rag FROM telegram_prefs") as cursor:
            rows = await cursor.fetchall()
        return {row["chat_id"]: bool(row["rag"]) for row in rows}
    finally:
        await db.close()


async def set_rag(chat_id: int, enabled: bool) -> None:
    """Persist the per-chat RAG toggle (/rag on|off)."""
    now = int(time.time())
    db = await _connect()
    try:
        await db.execute(
            "INSERT INTO telegram_prefs (chat_id, locale, rag, updated_at) "
            "VALUES (?, 'it', ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET rag = excluded.rag, updated_at = excluded.updated_at",
            (chat_id, int(enabled), now),
        )
        await db.commit()
        logger.info("telegram_prefs: chat_id=%s rag=%s", chat_id, enabled)
    finally:
        await db.close()


async def set_memory(chat_id: int, enabled: bool) -> None:
    """Persist the per-chat memory toggle (/memory on|off)."""
    now = int(time.time())
    db = await _connect()
    try:
        await db.execute(
            "INSERT INTO telegram_prefs (chat_id, locale, memory, updated_at) "
            "VALUES (?, 'it', ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET memory = excluded.memory, updated_at = excluded.updated_at",
            (chat_id, int(enabled), now),
        )
        await db.commit()
        logger.info("telegram_prefs: chat_id=%s memory=%s", chat_id, enabled)
    finally:
        await db.close()


async def set_locale(chat_id: int, locale: str) -> None:
    now = int(time.time())
    db = await _connect()
    try:
        await db.execute(
            "INSERT INTO telegram_prefs (chat_id, locale, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET locale = excluded.locale, updated_at = excluded.updated_at",
            (chat_id, locale, now),
        )
        await db.commit()
        logger.info("telegram_prefs: chat_id=%s locale=%s", chat_id, locale)
    finally:
        await db.close()
