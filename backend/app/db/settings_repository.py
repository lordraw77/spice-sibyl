"""
settings_repository — roaming preferences store (Phase 23).

A single JSON blob per owner_key. Two namespaces:
  * 'user:<uid>'    — a user's UI/appearance settings (theme, accent, locale, ...)
  * 'profile:<pid>' — a profile's chat settings (model, params, system prompt, ...)

The blob shape is owned by the frontend; this layer only persists opaque JSON.
"""

import json
import time

import aiosqlite


async def get(db: aiosqlite.Connection, owner_key: str) -> dict:
    """Return the stored blob for owner_key, or {} when none exists / is corrupt."""
    async with db.execute(
        "SELECT data FROM preferences WHERE owner_key = ?", (owner_key,)
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return {}
    try:
        parsed = json.loads(row["data"])
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def put(db: aiosqlite.Connection, owner_key: str, data: dict) -> None:
    """UPSERT the blob for owner_key."""
    now = int(time.time())
    await db.execute(
        "INSERT INTO preferences (owner_key, data, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(owner_key) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
        (owner_key, json.dumps(data), now),
    )
    await db.commit()
