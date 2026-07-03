"""
Phase 19 — feedback & evaluation endpoints.

Routes (under /v1/feedback):
  PUT    /messages/{message_id}   — set 👍/👎 (+ optional note) on an assistant message
  DELETE /messages/{message_id}   — clear feedback
  GET    /export                  — exportable dataset (prompt/response/rating) for offline eval
  GET    /stats                   — quick counts for the profile
"""

import json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.db import profile_repository
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.schemas.auth import UserOut

router = APIRouter()


class FeedbackIn(BaseModel):
    rating: int = Field(ge=-1, le=1)  # 1 = 👍, -1 = 👎
    note: str | None = Field(default=None, max_length=2000)


async def _assert_owns_message(
    db: aiosqlite.Connection, message_id: str, user: UserOut
) -> aiosqlite.Row:
    async with db.execute(
        "SELECT m.id, m.role, c.profile_id FROM messages m "
        "JOIN conversations c ON c.id = m.conversation_id WHERE m.id = ?",
        (message_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")
    profile = await profile_repository.get_profile(db, row["profile_id"])
    if not profile or profile.user_id != user.id:
        raise HTTPException(status_code=403, detail="Message does not belong to you")
    return row


@router.put("/messages/{message_id}")
async def set_feedback(
    message_id: str,
    body: FeedbackIn,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    row = await _assert_owns_message(db, message_id, user)
    if row["role"] != "assistant":
        raise HTTPException(status_code=422, detail="Feedback applies to assistant messages only")
    rating = body.rating if body.rating != 0 else None
    await db.execute(
        "UPDATE messages SET rating = ?, feedback_note = ? WHERE id = ?",
        (rating, (body.note or None), message_id),
    )
    await db.commit()
    return {"id": message_id, "rating": rating, "note": body.note or None}


@router.delete("/messages/{message_id}", status_code=204)
async def clear_feedback(
    message_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _assert_owns_message(db, message_id, user)
    await db.execute(
        "UPDATE messages SET rating = NULL, feedback_note = NULL WHERE id = ?",
        (message_id,),
    )
    await db.commit()


@router.get("/stats")
async def feedback_stats(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    async with db.execute(
        "SELECT SUM(CASE WHEN m.rating = 1 THEN 1 ELSE 0 END) AS up, "
        "       SUM(CASE WHEN m.rating = -1 THEN 1 ELSE 0 END) AS down "
        "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        "WHERE c.profile_id = ? AND m.rating IS NOT NULL",
        (profile_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return {"up": row["up"] or 0, "down": row["down"] or 0}


@router.get("/export")
async def export_feedback(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
) -> Response:
    """Rated assistant messages, each paired with the preceding user prompt —
    a ready-to-use dataset for offline evaluation / the regression harness."""
    async with db.execute(
        "SELECT m.id, m.conversation_id, m.content, m.model, m.provider, "
        "       m.rating, m.feedback_note, m.created_at, "
        "       (SELECT content FROM messages p "
        "        WHERE p.conversation_id = m.conversation_id AND p.role = 'user' "
        "          AND p.created_at <= m.created_at ORDER BY p.created_at DESC LIMIT 1) AS prompt "
        "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        "WHERE c.profile_id = ? AND m.rating IS NOT NULL "
        "ORDER BY m.created_at ASC",
        (profile_id,),
    ) as cursor:
        rows = await cursor.fetchall()

    dataset = [
        {
            "message_id": r["id"],
            "conversation_id": r["conversation_id"],
            "prompt": r["prompt"],
            "response": r["content"],
            "model": r["model"],
            "provider": r["provider"],
            "rating": r["rating"],
            "note": r["feedback_note"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return Response(
        content=json.dumps(dataset, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="feedback-dataset.json"'},
    )
