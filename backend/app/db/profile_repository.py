import time
import uuid

import aiosqlite

from app.schemas.profiles import Profile


def _row_to_profile(row: aiosqlite.Row) -> Profile:
    # user_id is absent on very old rows queried with a narrow projection; default None.
    keys = row.keys()
    return Profile(
        id=row["id"],
        name=row["name"],
        created_at=row["created_at"],
        user_id=row["user_id"] if "user_id" in keys else None,
    )


async def list_profiles(
    db: aiosqlite.Connection, user_id: str | None = None
) -> list[Profile]:
    """List profiles, scoped to a user when user_id is given (Phase 13)."""
    if user_id is not None:
        query = "SELECT * FROM profiles WHERE user_id = ? ORDER BY created_at ASC"
        params: tuple = (user_id,)
    else:
        query = "SELECT * FROM profiles ORDER BY created_at ASC"
        params = ()
    async with db.execute(query, params) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_profile(r) for r in rows]


async def get_profile(db: aiosqlite.Connection, profile_id: str) -> Profile | None:
    async with db.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)) as cursor:
        row = await cursor.fetchone()
    return _row_to_profile(row) if row else None


async def create_profile(
    db: aiosqlite.Connection, name: str, user_id: str | None = None
) -> Profile:
    profile_id = str(uuid.uuid4())
    now = int(time.time())
    await db.execute(
        "INSERT INTO profiles (id, name, created_at, user_id) VALUES (?, ?, ?, ?)",
        (profile_id, name.strip(), now, user_id),
    )
    await db.commit()
    return Profile(id=profile_id, name=name.strip(), created_at=now, user_id=user_id)


async def delete_profile(db: aiosqlite.Connection, profile_id: str) -> None:
    # Conversations are not FK-linked to profiles, delete them manually
    await db.execute("DELETE FROM conversations WHERE profile_id = ?", (profile_id,))
    await db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
    await db.commit()
