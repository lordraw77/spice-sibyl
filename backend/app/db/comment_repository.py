"""
comment_repository — persistence for Phase 20.b annotations & comments.

Threaded comments on a conversation or one of its messages. Threading is via
parent_id. Comments are soft-deleted (deleted=1, body blanked) so that replies
keep their anchor in the thread.
"""

import time
import uuid

import aiosqlite

from app.schemas.comments import CommentOut


def _row_to_comment(row: aiosqlite.Row) -> CommentOut:
    return CommentOut(
        id=row["id"],
        conversation_id=row["conversation_id"],
        message_id=row["message_id"],
        parent_id=row["parent_id"],
        user_id=row["user_id"],
        author_email=row["author_email"],
        body=row["body"],
        deleted=bool(row["deleted"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def list_for_conversation(
    db: aiosqlite.Connection, conversation_id: str
) -> list[CommentOut]:
    """All comments on a conversation (oldest first), joined with author email."""
    async with db.execute(
        """
        SELECT c.*, u.email AS author_email
        FROM comments c
        JOIN users u ON u.id = c.user_id
        WHERE c.conversation_id = ?
        ORDER BY c.created_at ASC
        """,
        (conversation_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_comment(r) for r in rows]


async def get_comment(db: aiosqlite.Connection, comment_id: str) -> CommentOut | None:
    async with db.execute(
        """
        SELECT c.*, u.email AS author_email
        FROM comments c
        JOIN users u ON u.id = c.user_id
        WHERE c.id = ?
        """,
        (comment_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return _row_to_comment(row) if row else None


async def create_comment(
    db: aiosqlite.Connection,
    conversation_id: str,
    user_id: str,
    body: str,
    message_id: str | None = None,
    parent_id: str | None = None,
) -> CommentOut:
    comment_id = str(uuid.uuid4())
    now = int(time.time())
    await db.execute(
        "INSERT INTO comments "
        "(id, conversation_id, message_id, parent_id, user_id, body, created_at, updated_at, deleted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (comment_id, conversation_id, message_id, parent_id, user_id, body.strip(), now, now),
    )
    await db.commit()
    created = await get_comment(db, comment_id)
    assert created is not None
    return created


async def update_comment(
    db: aiosqlite.Connection, comment_id: str, body: str
) -> CommentOut | None:
    now = int(time.time())
    await db.execute(
        "UPDATE comments SET body = ?, updated_at = ? WHERE id = ? AND deleted = 0",
        (body.strip(), now, comment_id),
    )
    await db.commit()
    return await get_comment(db, comment_id)


async def soft_delete(db: aiosqlite.Connection, comment_id: str) -> None:
    """Blank the body and flag deleted so child replies keep their anchor."""
    await db.execute(
        "UPDATE comments SET deleted = 1, body = '', updated_at = ? WHERE id = ?",
        (int(time.time()), comment_id),
    )
    await db.commit()
