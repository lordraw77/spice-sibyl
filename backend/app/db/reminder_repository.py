"""
reminder_repository — persistence for cross-channel reminders (Phase 23.d).

Replaces the Phase 14 telegram_reminders table: a reminder now optionally
belongs to a web profile (owner_profile_id), a Telegram chat (chat_id), or
both, and can recur (recurrence != 'once'). Firing is channel-agnostic and
owned by app.services.reminder_service's polling loop, not the Telegram
JobQueue — so uses a short-lived connection per call like the repository it
replaces.
"""

import time
import uuid

import aiosqlite

from app.core.config import settings
from app.db import pool

_COLUMNS = (
    "id, owner_profile_id, chat_id, text, smart_prompt, recurrence, fire_at, "
    "timezone, channels, active, fired, created_at, last_fired_at"
)


async def _connect() -> pool.PooledConnection:
    # Borrow from the shared pool; call sites' `await db.close()` release it back.
    return await pool.checkout()


async def create(
    *,
    owner_profile_id: str | None,
    chat_id: int | None,
    text: str | None,
    smart_prompt: str | None,
    recurrence: str,
    fire_at: int,
    timezone: str | None,
    channels: str,
) -> str:
    reminder_id = str(uuid.uuid4())
    now = int(time.time())
    db = await _connect()
    try:
        await db.execute(
            f"INSERT INTO reminders ({_COLUMNS}) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, NULL)",
            (
                reminder_id, owner_profile_id, chat_id, text, smart_prompt,
                recurrence, fire_at, timezone, channels, now,
            ),
        )
        await db.commit()
    finally:
        await db.close()
    return reminder_id


async def get(reminder_id: str) -> aiosqlite.Row | None:
    db = await _connect()
    try:
        async with db.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)) as cursor:
            return await cursor.fetchone()
    finally:
        await db.close()


async def list_for_profile(profile_id: str, include_inactive: bool = True) -> list[aiosqlite.Row]:
    db = await _connect()
    try:
        query = "SELECT * FROM reminders WHERE owner_profile_id = ?"
        if not include_inactive:
            query += " AND active = 1"
        query += " ORDER BY fire_at ASC"
        async with db.execute(query, (profile_id,)) as cursor:
            return await cursor.fetchall()
    finally:
        await db.close()


async def list_for_chat(chat_id: int) -> list[aiosqlite.Row]:
    db = await _connect()
    try:
        async with db.execute(
            "SELECT * FROM reminders WHERE chat_id = ? AND active = 1 AND fired = 0 "
            "ORDER BY fire_at ASC",
            (chat_id,),
        ) as cursor:
            return await cursor.fetchall()
    finally:
        await db.close()


async def list_all_active_due(before_ts: int) -> list[aiosqlite.Row]:
    """Reminders the polling loop should fire now: active, not yet fired, due."""
    db = await _connect()
    try:
        async with db.execute(
            "SELECT * FROM reminders WHERE active = 1 AND fired = 0 AND fire_at <= ? "
            "ORDER BY fire_at ASC",
            (before_ts,),
        ) as cursor:
            return await cursor.fetchall()
    finally:
        await db.close()


_UPDATABLE_FIELDS = frozenset({
    "text", "smart_prompt", "recurrence", "fire_at", "timezone", "channels",
    "active", "fired", "last_fired_at",
})


async def update(reminder_id: str, **fields) -> None:
    """Partial patch of a reminder row. Ignores unknown/empty field sets."""
    fields = {k: v for k, v in fields.items() if k in _UPDATABLE_FIELDS}
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    db = await _connect()
    try:
        await db.execute(
            f"UPDATE reminders SET {assignments} WHERE id = ?",
            (*fields.values(), reminder_id),
        )
        await db.commit()
    finally:
        await db.close()


async def delete(reminder_id: str, *, owner_profile_id: str | None = None, chat_id: int | None = None) -> bool:
    """Delete a reminder, optionally scoped to its owning profile or chat. Returns True if removed."""
    query = "DELETE FROM reminders WHERE id = ?"
    params: list = [reminder_id]
    if owner_profile_id is not None:
        query += " AND owner_profile_id = ?"
        params.append(owner_profile_id)
    if chat_id is not None:
        query += " AND chat_id = ?"
        params.append(chat_id)
    db = await _connect()
    try:
        cursor = await db.execute(query, params)
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()
