"""
telegram_reminder_repository — persistence for Telegram /remind reminders.

Reminders survive a bot restart: on startup the bot reloads pending rows and
re-schedules them on the JobQueue.  Uses a short-lived connection per call
(opened against settings.db_path) since the bot runs outside the FastAPI
dependency-injection lifecycle.
"""

import time
import uuid

import aiosqlite

from app.core.config import settings


async def _connect() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    return db


async def create(chat_id: int, user_id: int | None, text: str, fire_at: int) -> str:
    reminder_id = str(uuid.uuid4())
    now = int(time.time())
    db = await _connect()
    try:
        await db.execute(
            "INSERT INTO telegram_reminders (id, chat_id, user_id, text, fire_at, created_at, fired) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (reminder_id, chat_id, user_id, text, fire_at, now),
        )
        await db.commit()
    finally:
        await db.close()
    return reminder_id


async def list_pending(chat_id: int) -> list[aiosqlite.Row]:
    db = await _connect()
    try:
        async with db.execute(
            "SELECT * FROM telegram_reminders WHERE chat_id = ? AND fired = 0 "
            "ORDER BY fire_at ASC",
            (chat_id,),
        ) as cursor:
            return await cursor.fetchall()
    finally:
        await db.close()


async def list_all_pending() -> list[aiosqlite.Row]:
    db = await _connect()
    try:
        async with db.execute(
            "SELECT * FROM telegram_reminders WHERE fired = 0 ORDER BY fire_at ASC"
        ) as cursor:
            return await cursor.fetchall()
    finally:
        await db.close()


async def get(reminder_id: str) -> aiosqlite.Row | None:
    db = await _connect()
    try:
        async with db.execute(
            "SELECT * FROM telegram_reminders WHERE id = ?", (reminder_id,)
        ) as cursor:
            return await cursor.fetchone()
    finally:
        await db.close()


async def mark_fired(reminder_id: str) -> None:
    db = await _connect()
    try:
        await db.execute(
            "UPDATE telegram_reminders SET fired = 1 WHERE id = ?", (reminder_id,)
        )
        await db.commit()
    finally:
        await db.close()


async def delete(reminder_id: str, chat_id: int) -> bool:
    """Delete a reminder scoped to a chat. Returns True if a row was removed."""
    db = await _connect()
    try:
        cursor = await db.execute(
            "DELETE FROM telegram_reminders WHERE id = ? AND chat_id = ?",
            (reminder_id, chat_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()
