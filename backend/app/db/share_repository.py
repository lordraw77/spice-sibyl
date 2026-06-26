import secrets
import time

import aiosqlite

from app.db.conversation_repository import get_conversation
from app.schemas.conversations import Conversation


async def create_share(
    db: aiosqlite.Connection, conversation_id: str
) -> str:
    conv = await get_conversation(db, conversation_id)
    if not conv:
        raise ValueError("Conversation not found")

    async with db.execute(
        "SELECT share_token FROM shared_conversations WHERE conversation_id = ?",
        (conversation_id,),
    ) as cursor:
        existing = await cursor.fetchone()
    if existing:
        return existing["share_token"]

    token = secrets.token_urlsafe(9)
    now = int(time.time())
    await db.execute(
        "INSERT INTO shared_conversations (share_token, conversation_id, created_at) VALUES (?, ?, ?)",
        (token, conversation_id, now),
    )
    await db.commit()
    return token


async def get_shared_conversation(
    db: aiosqlite.Connection, share_token: str
) -> Conversation | None:
    async with db.execute(
        "SELECT conversation_id FROM shared_conversations WHERE share_token = ?",
        (share_token,),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    return await get_conversation(db, row["conversation_id"])


async def delete_share(
    db: aiosqlite.Connection, conversation_id: str
) -> None:
    await db.execute(
        "DELETE FROM shared_conversations WHERE conversation_id = ?",
        (conversation_id,),
    )
    await db.commit()


async def get_share_token(
    db: aiosqlite.Connection, conversation_id: str
) -> str | None:
    async with db.execute(
        "SELECT share_token FROM shared_conversations WHERE conversation_id = ?",
        (conversation_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return row["share_token"] if row else None
