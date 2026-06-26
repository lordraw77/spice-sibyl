import time

import aiosqlite


async def link(
    db: aiosqlite.Connection, telegram_id: int, profile_id: str, username: str | None = None
) -> None:
    now = int(time.time())
    await db.execute(
        "INSERT OR REPLACE INTO telegram_links (telegram_id, profile_id, username, linked_at) VALUES (?, ?, ?, ?)",
        (telegram_id, profile_id, username, now),
    )
    await db.commit()


async def unlink_by_profile(db: aiosqlite.Connection, profile_id: str) -> None:
    await db.execute("DELETE FROM telegram_links WHERE profile_id = ?", (profile_id,))
    await db.commit()


async def get_by_telegram_id(
    db: aiosqlite.Connection, telegram_id: int
) -> dict | None:
    async with db.execute(
        "SELECT * FROM telegram_links WHERE telegram_id = ?", (telegram_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    return {"telegram_id": row["telegram_id"], "profile_id": row["profile_id"], "username": row["username"], "linked_at": row["linked_at"]}


async def get_by_profile_id(
    db: aiosqlite.Connection, profile_id: str
) -> dict | None:
    async with db.execute(
        "SELECT * FROM telegram_links WHERE profile_id = ?", (profile_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    return {"telegram_id": row["telegram_id"], "profile_id": row["profile_id"], "username": row["username"], "linked_at": row["linked_at"]}
