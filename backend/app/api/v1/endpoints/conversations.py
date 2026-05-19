"""
Conversation persistence endpoints.

Route map (all under /v1/conversations):
  GET    /                       — list conversations for the current profile
  POST   /                       — create a new conversation
  GET    /{id}                   — get conversation with full message history
  PATCH  /{id}                   — rename a conversation
  DELETE /{id}                   — delete conversation and all its messages
  POST   /{id}/messages          — append messages to an existing conversation

Profile identity is conveyed via the X-Profile-ID request header.
Missing or empty header falls back to 'default'.
"""

import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.db import conversation_repository as repo
from app.db.database import get_db
from app.schemas.conversations import (
    AppendMessagesRequest,
    Conversation,
    ConversationCreate,
    ConversationSummary,
    ConversationUpdate,
)

router = APIRouter()

_DEFAULT_PROFILE = "default"


def _profile(x_profile_id: str | None = Header(default=None)) -> str:
    return x_profile_id or _DEFAULT_PROFILE


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    profile_id: str = Query(default=_DEFAULT_PROFILE),
    db: aiosqlite.Connection = Depends(get_db),
):
    return await repo.list_conversations(db, profile_id)


@router.post("", response_model=ConversationSummary, status_code=201)
async def create_conversation(
    body: ConversationCreate,
    profile_id: str = Depends(_profile),
    db: aiosqlite.Connection = Depends(get_db),
):
    pid = body.profile_id or profile_id
    return await repo.create_conversation(db, body.title, body.model, pid)


@router.get("/{conversation_id}", response_model=Conversation)
async def get_conversation(
    conversation_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    conv = await repo.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.patch("/{conversation_id}", response_model=ConversationSummary)
async def rename_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    db: aiosqlite.Connection = Depends(get_db),
):
    conv = await repo.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await repo.update_title(db, conversation_id, body.title)
    updated = await repo.get_conversation(db, conversation_id)
    return ConversationSummary(**updated.model_dump(exclude={"messages"}))


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    await repo.delete_conversation(db, conversation_id)


@router.post("/{conversation_id}/messages", status_code=204)
async def append_messages(
    conversation_id: str,
    body: AppendMessagesRequest,
    db: aiosqlite.Connection = Depends(get_db),
):
    conv = await repo.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await repo.append_messages(db, conversation_id, body.messages)
