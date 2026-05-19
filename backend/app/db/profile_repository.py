import time
import uuid

import aiosqlite

from app.schemas.profiles import Profile


def _row_to_profile(row: aiosqlite.Row) -> Profile:
    return Profile(id=row["id"], name=row["name"], created_at=row["created_at"])


async def list_profiles(db: aiosqlite.Connection) -> list[Profile]:
    async with db.execute("SELECT * FROM profiles ORDER BY created_at ASC") as cursor:
        rows = await cursor.fetchall()
    return [_row_to_profile(r) for r in rows]


async def get_profile(db: aiosqlite.Connection, profile_id: str) -> Profile | None:
    async with db.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)) as cursor:
        row = await cursor.fetchone()
    return _row_to_profile(row) if row else None


async def create_profile(db: aiosqlite.Connection, name: str) -> Profile:
    profile_id = str(uuid.uuid4())
    now = int(time.time())
    await db.execute(
        "INSERT INTO profiles (id, name, created_at) VALUES (?, ?, ?)",
        (profile_id, name.strip(), now),
    )
    await db.commit()
    return Profile(id=profile_id, name=name.strip(), created_at=now)


async def delete_profile(db: aiosqlite.Connection, profile_id: str) -> None:
    # Conversations are not FK-linked to profiles, delete them manually
    await db.execute("DELETE FROM conversations WHERE profile_id = ?", (profile_id,))
    await db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
    await db.commit()
