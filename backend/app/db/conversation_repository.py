import json
import time
import uuid

import aiosqlite

from app.schemas.chat import ChatMessage
from app.schemas.conversations import Conversation, ConversationSummary


def _row_to_summary(row: aiosqlite.Row) -> ConversationSummary:
    return ConversationSummary(
        id=row["id"],
        title=row["title"],
        model=row["model"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_message(row: aiosqlite.Row) -> ChatMessage:
    caps = row["capabilities"]
    free_val = row["free"]
    keys = row.keys()
    pinned_val = row["pinned"] if "pinned" in keys else None
    parent_id = row["parent_id"] if "parent_id" in keys else None
    branch_index = row["branch_index"] if "branch_index" in keys else None
    return ChatMessage(
        id=row["id"],
        role=row["role"],
        content=row["content"],
        model=row["model"],
        provider=row["provider"],
        latency_ms=row["latency_ms"],
        first_token_ms=row["first_token_ms"],
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        total_tokens=row["total_tokens"],
        tokens_per_second=row["tokens_per_second"],
        finish_reason=row["finish_reason"],
        estimated_cost=row["estimated_cost"],
        created_at=row["created_at"],
        capabilities=json.loads(caps) if caps else None,
        free=bool(free_val) if free_val is not None else None,
        pinned=bool(pinned_val) if pinned_val is not None else None,
        parent_id=parent_id,
        branch_index=branch_index,
    )


async def list_conversations(
    db: aiosqlite.Connection, profile_id: str
) -> list[ConversationSummary]:
    async with db.execute(
        "SELECT * FROM conversations WHERE profile_id = ? ORDER BY updated_at DESC",
        (profile_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_summary(r) for r in rows]


async def get_conversation(
    db: aiosqlite.Connection, conversation_id: str
) -> Conversation | None:
    async with db.execute(
        "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    async with db.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
        (conversation_id,),
    ) as cursor:
        msg_rows = await cursor.fetchall()
    return Conversation(
        **dict(row),
        messages=[_row_to_message(r) for r in msg_rows],
    )


async def create_conversation(
    db: aiosqlite.Connection, title: str, model: str, profile_id: str
) -> ConversationSummary:
    conversation_id = str(uuid.uuid4())
    now = int(time.time())
    await db.execute(
        "INSERT INTO conversations (id, profile_id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (conversation_id, profile_id, title, model, now, now),
    )
    await db.commit()
    return ConversationSummary(
        id=conversation_id, title=title, model=model, created_at=now, updated_at=now
    )


async def update_title(
    db: aiosqlite.Connection, conversation_id: str, title: str
) -> int:
    """Update the conversation title and return the new updated_at timestamp."""
    now = int(time.time())
    await db.execute(
        "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
        (title, now, conversation_id),
    )
    await db.commit()
    return now


async def delete_conversation(
    db: aiosqlite.Connection, conversation_id: str
) -> None:
    await db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    await db.commit()


async def toggle_pin(
    db: aiosqlite.Connection, message_id: str
) -> bool:
    async with db.execute(
        "SELECT pinned FROM messages WHERE id = ?", (message_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return False
    new_val = 0 if row["pinned"] else 1
    await db.execute(
        "UPDATE messages SET pinned = ? WHERE id = ?", (new_val, message_id)
    )
    await db.commit()
    return bool(new_val)


async def get_pinned_messages(
    db: aiosqlite.Connection, conversation_id: str
) -> list[ChatMessage]:
    async with db.execute(
        "SELECT * FROM messages WHERE conversation_id = ? AND pinned = 1 ORDER BY created_at ASC",
        (conversation_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_message(r) for r in rows]


async def get_branch_siblings(
    db: aiosqlite.Connection, conversation_id: str, parent_id: str
) -> list[ChatMessage]:
    async with db.execute(
        "SELECT * FROM messages WHERE conversation_id = ? AND parent_id = ? ORDER BY branch_index ASC",
        (conversation_id, parent_id),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_message(r) for r in rows]


async def append_messages(
    db: aiosqlite.Connection, conversation_id: str, messages: list[ChatMessage]
) -> None:
    now = int(time.time())
    for msg in messages:
        content = (
            msg.content
            if isinstance(msg.content, str)
            else json.dumps(msg.content)
        )
        msg_id = msg.id or str(uuid.uuid4())
        await db.execute(
            """INSERT INTO messages
               (id, conversation_id, role, content, model, provider,
                latency_ms, first_token_ms, prompt_tokens, completion_tokens,
                total_tokens, tokens_per_second, finish_reason, estimated_cost,
                created_at, capabilities, free, pinned, parent_id, branch_index)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg_id,
                conversation_id,
                msg.role,
                content,
                msg.model,
                msg.provider,
                msg.latency_ms,
                msg.first_token_ms,
                msg.prompt_tokens,
                msg.completion_tokens,
                msg.total_tokens,
                msg.tokens_per_second,
                msg.finish_reason,
                msg.estimated_cost,
                msg.created_at or now,
                json.dumps(msg.capabilities) if msg.capabilities else None,
                int(msg.free) if msg.free is not None else None,
                int(msg.pinned) if msg.pinned else 0,
                msg.parent_id,
                msg.branch_index or 0,
            ),
        )
    await db.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?",
        (now, conversation_id),
    )
    await db.commit()
