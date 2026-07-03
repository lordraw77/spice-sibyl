"""
memory_repository — persistence for Phase 19 per-profile memories.

One row per remembered fact. The chat pipeline injects enabled memories into
the system prompt; the async extraction task adds/updates/deletes rows after
each exchange.
"""

import time
import uuid

import aiosqlite

from app.schemas.memories import MemoryOut

_CATEGORIES = frozenset({"preference", "fact", "project", "instruction"})


def _row_to_memory(row: aiosqlite.Row) -> MemoryOut:
    return MemoryOut(
        id=row["id"],
        profile_id=row["profile_id"],
        content=row["content"],
        category=row["category"],
        source_conversation_id=row["source_conversation_id"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def list_memories(
    db: aiosqlite.Connection, profile_id: str, enabled_only: bool = False
) -> list[MemoryOut]:
    enabled_filter = "AND enabled = 1" if enabled_only else ""
    async with db.execute(
        f"SELECT * FROM profile_memories WHERE profile_id = ? {enabled_filter} "
        "ORDER BY updated_at DESC",
        (profile_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_memory(r) for r in rows]


async def get_memory(db: aiosqlite.Connection, memory_id: str) -> MemoryOut | None:
    async with db.execute(
        "SELECT * FROM profile_memories WHERE id = ?", (memory_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return _row_to_memory(row) if row else None


async def create_memory(
    db: aiosqlite.Connection,
    profile_id: str,
    content: str,
    category: str = "fact",
    source_conversation_id: str | None = None,
) -> MemoryOut:
    if category not in _CATEGORIES:
        category = "fact"
    memory_id = str(uuid.uuid4())
    now = int(time.time())
    await db.execute(
        "INSERT INTO profile_memories "
        "(id, profile_id, content, category, source_conversation_id, enabled, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
        (memory_id, profile_id, content.strip(), category, source_conversation_id, now, now),
    )
    await db.commit()
    return MemoryOut(
        id=memory_id,
        profile_id=profile_id,
        content=content.strip(),
        category=category,
        source_conversation_id=source_conversation_id,
        enabled=True,
        created_at=now,
        updated_at=now,
    )


async def update_memory(
    db: aiosqlite.Connection,
    memory_id: str,
    content: str | None = None,
    category: str | None = None,
    enabled: bool | None = None,
) -> MemoryOut | None:
    existing = await get_memory(db, memory_id)
    if not existing:
        return None
    new_content = content.strip() if content is not None else existing.content
    new_category = category if category in _CATEGORIES else existing.category
    new_enabled = int(enabled) if enabled is not None else int(existing.enabled)
    now = int(time.time())
    await db.execute(
        "UPDATE profile_memories SET content = ?, category = ?, enabled = ?, updated_at = ? WHERE id = ?",
        (new_content, new_category, new_enabled, now, memory_id),
    )
    await db.commit()
    return await get_memory(db, memory_id)


async def delete_memory(db: aiosqlite.Connection, memory_id: str) -> bool:
    cursor = await db.execute("DELETE FROM profile_memories WHERE id = ?", (memory_id,))
    await db.commit()
    return cursor.rowcount > 0


async def delete_all(db: aiosqlite.Connection, profile_id: str) -> int:
    cursor = await db.execute(
        "DELETE FROM profile_memories WHERE profile_id = ?", (profile_id,)
    )
    await db.commit()
    return cursor.rowcount


async def count_memories(db: aiosqlite.Connection, profile_id: str) -> int:
    async with db.execute(
        "SELECT COUNT(*) AS n FROM profile_memories WHERE profile_id = ?", (profile_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return row["n"] if row else 0


async def get_memory_enabled(db: aiosqlite.Connection, profile_id: str) -> bool:
    """Per-profile memory switch; defaults to True for unknown/legacy profiles."""
    async with db.execute(
        "SELECT memory_enabled FROM profiles WHERE id = ?", (profile_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return True
    keys = row.keys()
    return bool(row["memory_enabled"]) if "memory_enabled" in keys else True


async def set_memory_enabled(
    db: aiosqlite.Connection, profile_id: str, enabled: bool
) -> None:
    await db.execute(
        "UPDATE profiles SET memory_enabled = ? WHERE id = ?",
        (int(enabled), profile_id),
    )
    await db.commit()
