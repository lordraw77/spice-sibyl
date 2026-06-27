"""User account persistence (Phase 13 auth)."""

import time
import uuid

import aiosqlite

from app.schemas.auth import UserOut

VALID_ROLES = frozenset({"admin", "user", "read-only"})


def _row_to_user(row: aiosqlite.Row) -> UserOut:
    return UserOut(
        id=row["id"],
        email=row["email"],
        role=row["role"],
        disabled=bool(row["disabled"]),
        created_at=row["created_at"],
    )


async def create_user(
    db: aiosqlite.Connection,
    email: str,
    password_hash: str,
    role: str = "user",
) -> UserOut:
    user_id = str(uuid.uuid4())
    now = int(time.time())
    await db.execute(
        "INSERT INTO users (id, email, password_hash, role, created_at, disabled) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        (user_id, email.strip().lower(), password_hash, role, now),
    )
    await db.commit()
    return UserOut(id=user_id, email=email.strip().lower(), role=role, disabled=False, created_at=now)


async def get_by_email(db: aiosqlite.Connection, email: str) -> aiosqlite.Row | None:
    """Return the raw row (includes password_hash) for authentication."""
    async with db.execute(
        "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
    ) as cursor:
        return await cursor.fetchone()


async def get_by_id(db: aiosqlite.Connection, user_id: str) -> aiosqlite.Row | None:
    async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
        return await cursor.fetchone()


async def list_users(db: aiosqlite.Connection) -> list[UserOut]:
    async with db.execute("SELECT * FROM users ORDER BY created_at ASC") as cursor:
        rows = await cursor.fetchall()
    return [_row_to_user(r) for r in rows]


async def count_users(db: aiosqlite.Connection) -> int:
    async with db.execute("SELECT COUNT(*) AS n FROM users") as cursor:
        row = await cursor.fetchone()
    return row["n"] if row else 0


async def set_role(db: aiosqlite.Connection, user_id: str, role: str) -> None:
    await db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    await db.commit()


async def set_disabled(db: aiosqlite.Connection, user_id: str, disabled: bool) -> None:
    await db.execute(
        "UPDATE users SET disabled = ? WHERE id = ?", (1 if disabled else 0, user_id)
    )
    await db.commit()
