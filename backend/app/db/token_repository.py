"""Refresh-token tracking for rotation and revocation (Phase 13)."""

import aiosqlite


async def store(
    db: aiosqlite.Connection, jti: str, user_id: str, expires_at: int
) -> None:
    await db.execute(
        "INSERT INTO refresh_tokens (jti, user_id, expires_at, revoked) VALUES (?, ?, ?, 0)",
        (jti, user_id, expires_at),
    )
    await db.commit()


async def is_active(db: aiosqlite.Connection, jti: str) -> bool:
    async with db.execute(
        "SELECT revoked FROM refresh_tokens WHERE jti = ?", (jti,)
    ) as cursor:
        row = await cursor.fetchone()
    return bool(row) and not row["revoked"]


async def revoke(db: aiosqlite.Connection, jti: str) -> None:
    await db.execute("UPDATE refresh_tokens SET revoked = 1 WHERE jti = ?", (jti,))
    await db.commit()


async def revoke_all_for_user(db: aiosqlite.Connection, user_id: str) -> None:
    await db.execute(
        "UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ?", (user_id,)
    )
    await db.commit()
