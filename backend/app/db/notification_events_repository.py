"""
notification_events_repository — persisted Telegram → web notification events
(Phase 23.c). Backs the badge/unread count and the notification list so events
survive when no SSE stream is open; live delivery is a separate in-memory
fan-out handled by notification_service.
"""

import json
import time
import uuid

import aiosqlite


async def create(
    db: aiosqlite.Connection,
    user_id: str,
    profile_id: str,
    event_type: str,
    title: str,
    body: str = "",
    meta: dict | None = None,
) -> dict:
    event_id = uuid.uuid4().hex
    now = int(time.time())
    await db.execute(
        "INSERT INTO notification_events "
        "(id, user_id, profile_id, event_type, title, body, meta_json, created_at, read) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (event_id, user_id, profile_id, event_type, title, body, json.dumps(meta) if meta else None, now),
    )
    await db.commit()
    return {
        "id": event_id,
        "user_id": user_id,
        "profile_id": profile_id,
        "event_type": event_type,
        "title": title,
        "body": body,
        "meta": meta,
        "created_at": now,
        "read": False,
    }


def _row_to_dict(row: aiosqlite.Row) -> dict:
    meta_raw = row["meta_json"]
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "profile_id": row["profile_id"],
        "event_type": row["event_type"],
        "title": row["title"],
        "body": row["body"],
        "meta": json.loads(meta_raw) if meta_raw else None,
        "created_at": row["created_at"],
        "read": bool(row["read"]),
    }


async def list_for_user(db: aiosqlite.Connection, user_id: str, limit: int = 50) -> list[dict]:
    async with db.execute(
        "SELECT * FROM notification_events WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_dict(row) for row in rows]


async def unread_count(db: aiosqlite.Connection, user_id: str) -> int:
    async with db.execute(
        "SELECT COUNT(*) AS n FROM notification_events WHERE user_id = ? AND read = 0",
        (user_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return row["n"] if row else 0


async def mark_read(db: aiosqlite.Connection, user_id: str, event_id: str) -> bool:
    cursor = await db.execute(
        "UPDATE notification_events SET read = 1 WHERE id = ? AND user_id = ?",
        (event_id, user_id),
    )
    await db.commit()
    return cursor.rowcount > 0
