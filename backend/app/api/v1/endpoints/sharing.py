"""
Conversation sharing endpoints.

Route map:
  POST   /v1/conversations/{id}/share  — create a shareable link
  DELETE /v1/conversations/{id}/share  — revoke sharing
  GET    /v1/shared/{token}            — get shared conversation (public, no auth)
"""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.db import share_repository as share_repo
from app.db.database import get_db
from app.schemas.conversations import Conversation

router = APIRouter()


@router.post("/conversations/{conversation_id}/share")
async def share_conversation(
    conversation_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    try:
        token = await share_repo.create_share(db, conversation_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversation not found")

    base_url = str(request.base_url).rstrip("/")
    share_url = f"{base_url}/shared/{token}"
    return {"share_token": token, "url": share_url}


@router.delete("/conversations/{conversation_id}/share", status_code=204)
async def unshare_conversation(
    conversation_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    await share_repo.delete_share(db, conversation_id)


@router.get("/shared/{share_token}", response_model=Conversation)
async def get_shared(
    share_token: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    conv = await share_repo.get_shared_conversation(db, share_token)
    if not conv:
        raise HTTPException(status_code=404, detail="Shared conversation not found or link expired")
    return conv
