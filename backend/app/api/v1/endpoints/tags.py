import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.db import tag_repository as repo
from app.db.database import get_db
from app.schemas.tags import Tag, TagCreate, TagUpdate, SetConversationTags

router = APIRouter()

_DEFAULT_PROFILE = "default"


def _profile(x_profile_id: str | None = Header(default=None)) -> str:
    return x_profile_id or _DEFAULT_PROFILE


@router.get("", response_model=list[Tag])
async def list_tags(
    profile_id: str = Query(default=_DEFAULT_PROFILE),
    db: aiosqlite.Connection = Depends(get_db),
):
    return await repo.list_tags(db, profile_id)


@router.post("", response_model=Tag, status_code=201)
async def create_tag(
    body: TagCreate,
    profile_id: str = Depends(_profile),
    db: aiosqlite.Connection = Depends(get_db),
):
    pid = body.profile_id or profile_id
    return await repo.create_tag(db, body.name, body.color, pid)


@router.patch("/{tag_id}", response_model=Tag)
async def update_tag(
    tag_id: str,
    body: TagUpdate,
    db: aiosqlite.Connection = Depends(get_db),
):
    result = await repo.update_tag(db, tag_id, body.name, body.color)
    if not result:
        raise HTTPException(status_code=404, detail="Tag not found")
    return result


@router.delete("/{tag_id}", status_code=204)
async def delete_tag(
    tag_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    await repo.delete_tag(db, tag_id)


@router.put("/conversations/{conversation_id}", status_code=204)
async def set_conversation_tags(
    conversation_id: str,
    body: SetConversationTags,
    db: aiosqlite.Connection = Depends(get_db),
):
    await repo.set_conversation_tags(db, conversation_id, body.tag_ids)


@router.get("/conversations/{conversation_id}", response_model=list[Tag])
async def get_conversation_tags(
    conversation_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    return await repo.get_conversation_tags(db, conversation_id)
