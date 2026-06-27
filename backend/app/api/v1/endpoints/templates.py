import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from app.db import template_repository as repo
from app.db.database import get_db
from app.dependencies.auth import resolve_profile
from app.schemas.templates import PromptTemplate, PromptTemplateCreate, PromptTemplateUpdate

router = APIRouter()


@router.get("", response_model=list[PromptTemplate])
async def list_templates(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    return await repo.list_templates(db, profile_id)


@router.post("", response_model=PromptTemplate, status_code=201)
async def create_template(
    body: PromptTemplateCreate,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    return await repo.create_template(db, body.name, body.content, profile_id)


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
