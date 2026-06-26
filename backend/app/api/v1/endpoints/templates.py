import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.db import template_repository as repo
from app.db.database import get_db
from app.schemas.templates import PromptTemplate, PromptTemplateCreate, PromptTemplateUpdate

router = APIRouter()

_DEFAULT_PROFILE = "default"


def _profile(x_profile_id: str | None = Header(default=None)) -> str:
    return x_profile_id or _DEFAULT_PROFILE


@router.get("", response_model=list[PromptTemplate])
async def list_templates(
    profile_id: str = Query(default=_DEFAULT_PROFILE),
    db: aiosqlite.Connection = Depends(get_db),
):
    return await repo.list_templates(db, profile_id)


@router.post("", response_model=PromptTemplate, status_code=201)
async def create_template(
    body: PromptTemplateCreate,
    profile_id: str = Depends(_profile),
    db: aiosqlite.Connection = Depends(get_db),
):
    pid = body.profile_id or profile_id
    return await repo.create_template(db, body.name, body.content, pid)


@router.patch("/{template_id}", response_model=PromptTemplate)
async def update_template(
    template_id: str,
    body: PromptTemplateUpdate,
    db: aiosqlite.Connection = Depends(get_db),
):
    result = await repo.update_template(db, template_id, body.name, body.content)
    if not result:
        raise HTTPException(status_code=404, detail="Template not found")
    return result


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    await repo.delete_template(db, template_id)
