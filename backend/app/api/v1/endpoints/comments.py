"""
Phase 20.b — annotations & comments endpoints.

Threaded comments on a conversation or one of its messages. Anyone who can
access the conversation (its owner, or a member of a workspace it is shared
into) can read and post; a comment can only be edited or deleted by its author.

Routes (under /v1/conversations/{conversation_id}/comments):
  GET    /              — list all comments on the conversation (threaded client-side)
  POST   /              — add a comment (optionally targeting a message / parent)
  PATCH  /{comment_id}  — edit own comment
  DELETE /{comment_id}  — soft-delete own comment
"""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from app.db import comment_repository as repo
from app.db import workspace_repository
from app.db.database import get_db
from app.dependencies.auth import get_current_user
from app.schemas.auth import UserOut
from app.schemas.comments import CommentCreate, CommentOut, CommentUpdate

router = APIRouter()


async def _require_access(
    db: aiosqlite.Connection, conversation_id: str, user: UserOut
) -> None:
    if not await workspace_repository.can_access_conversation(db, conversation_id, user.id):
        # 404 rather than 403 so we don't leak the existence of conversations the
        # caller has no relationship with.
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.get("/{conversation_id}/comments", response_model=list[CommentOut])
async def list_comments(
    conversation_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _require_access(db, conversation_id, user)
    return await repo.list_for_conversation(db, conversation_id)


@router.post("/{conversation_id}/comments", response_model=CommentOut, status_code=201)
async def create_comment(
    conversation_id: str,
    body: CommentCreate,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _require_access(db, conversation_id, user)

    if body.message_id is not None:
        async with db.execute(
            "SELECT 1 FROM messages WHERE id = ? AND conversation_id = ?",
            (body.message_id, conversation_id),
        ) as cursor:
            if not await cursor.fetchone():
                raise HTTPException(status_code=404, detail="Message not found")

    if body.parent_id is not None:
        parent = await repo.get_comment(db, body.parent_id)
        if not parent or parent.conversation_id != conversation_id:
            raise HTTPException(status_code=404, detail="Parent comment not found")

    return await repo.create_comment(
        db, conversation_id, user.id, body.body,
        message_id=body.message_id, parent_id=body.parent_id,
    )


@router.patch("/{conversation_id}/comments/{comment_id}", response_model=CommentOut)
async def update_comment(
    conversation_id: str,
    comment_id: str,
    body: CommentUpdate,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _require_access(db, conversation_id, user)
    existing = await repo.get_comment(db, comment_id)
    if not existing or existing.conversation_id != conversation_id or existing.deleted:
        raise HTTPException(status_code=404, detail="Comment not found")
    if existing.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your comment")
    return await repo.update_comment(db, comment_id, body.body)


@router.delete("/{conversation_id}/comments/{comment_id}", status_code=204)
async def delete_comment(
    conversation_id: str,
    comment_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _require_access(db, conversation_id, user)
    existing = await repo.get_comment(db, comment_id)
    if not existing or existing.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Comment not found")
    if existing.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your comment")
    await repo.soft_delete(db, comment_id)
