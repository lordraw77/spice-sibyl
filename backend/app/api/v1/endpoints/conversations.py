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

import json
import time

import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response

from app.db import conversation_repository as repo
from app.db import search_repository as search_repo
from app.db import tag_repository as tag_repo
from app.db.database import get_db
from app.schemas.conversations import (
    AppendMessagesRequest,
    Conversation,
    ConversationCreate,
    ConversationSummary,
    ConversationUpdate,
    SearchResult,
)

router = APIRouter()

_DEFAULT_PROFILE = "default"


def _profile(x_profile_id: str | None = Header(default=None)) -> str:
    return x_profile_id or _DEFAULT_PROFILE


@router.get("/search", response_model=list[SearchResult])
async def search_conversations(
    q: str = Query(default=""),
    profile_id: str | None = Query(default=None),
    db: aiosqlite.Connection = Depends(get_db),
):
    return await search_repo.search_conversations(db, q, profile_id=profile_id)


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    profile_id: str = Query(default=_DEFAULT_PROFILE),
    db: aiosqlite.Connection = Depends(get_db),
):
    convs = await repo.list_conversations(db, profile_id)
    conv_ids = [c.id for c in convs]
    tags_map = await tag_repo.get_tags_for_conversations(db, conv_ids)
    for c in convs:
        c.tags = tags_map.get(c.id, [])
    return convs


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
    # Lightweight existence check — avoids loading all messages just to verify 404.
    async with db.execute(
        "SELECT id, profile_id, model, created_at FROM conversations WHERE id = ?",
        (conversation_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")

    now = await repo.update_title(db, conversation_id, body.title)
    return ConversationSummary(
        id=row["id"],
        title=body.title,
        model=row["model"],
        created_at=row["created_at"],
        updated_at=now,
    )


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    await repo.delete_conversation(db, conversation_id)


@router.get("/{conversation_id}/export")
async def export_conversation(
    conversation_id: str,
    format: str = Query(default="md", pattern="^(md|json)$"),
    db: aiosqlite.Connection = Depends(get_db),
) -> Response:
    conv = await repo.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if format == "json":
        body = conv.model_dump_json(indent=2)
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="conversation-{conversation_id}.json"'},
        )

    # Markdown export
    ts = time.strftime("%Y-%m-%d %H:%M", time.gmtime(conv.created_at or 0))
    lines = [
        "---",
        f"title: {conv.title}",
        f"model: {conv.model}",
        f"date: {ts}",
        "---",
        "",
        f"# {conv.title}",
        "",
    ]
    for msg in conv.messages:
        role_label = "## User" if msg.role == "user" else f"## Assistant ({msg.model or 'AI'})"
        content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        lines += [role_label, "", content, ""]

    body_md = "\n".join(lines)
    return Response(
        content=body_md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="conversation-{conversation_id}.md"'},
    )


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


@router.patch("/{conversation_id}/messages/{message_id}/pin")
async def toggle_pin(
    conversation_id: str,
    message_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    pinned = await repo.toggle_pin(db, message_id)
    return {"pinned": pinned}


@router.get("/{conversation_id}/pins", response_model=list)
async def get_pinned_messages(
    conversation_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    return await repo.get_pinned_messages(db, conversation_id)


@router.get("/{conversation_id}/branches")
async def get_branches(
    conversation_id: str,
    parent_id: str = Query(...),
    db: aiosqlite.Connection = Depends(get_db),
):
    return await repo.get_branch_siblings(db, conversation_id, parent_id)
