"""
Phase 19 — per-profile persistent memory endpoints.

Routes (under /v1/memories):
  GET    /                — list the profile's memories
  POST   /                — add a memory manually
  PATCH  /{id}            — edit content/category or toggle a single memory
  DELETE /{id}            — forget one memory
  DELETE /                — forget everything for the profile
  GET    /settings        — per-profile memory switch
  PUT    /settings        — flip the per-profile memory switch
"""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.db import audit_repository, memory_repository
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.schemas.auth import UserOut
from app.schemas.memories import (
    MemoryCreate,
    MemoryOut,
    MemorySettingsOut,
    MemorySettingsUpdate,
    MemoryUpdate,
)

router = APIRouter()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/settings", response_model=MemorySettingsOut)
async def get_settings(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    enabled = await memory_repository.get_memory_enabled(db, profile_id)
    return MemorySettingsOut(memory_enabled=enabled)


@router.put("/settings", response_model=MemorySettingsOut)
async def update_settings(
    body: MemorySettingsUpdate,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await memory_repository.set_memory_enabled(db, profile_id, body.memory_enabled)
    return MemorySettingsOut(memory_enabled=body.memory_enabled)


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    return await memory_repository.list_memories(db, profile_id)


@router.post("", response_model=MemoryOut, status_code=201)
async def create_memory(
    body: MemoryCreate,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    return await memory_repository.create_memory(
        db, profile_id, body.content, category=body.category
    )


@router.patch("/{memory_id}", response_model=MemoryOut)
async def update_memory(
    memory_id: str,
    body: MemoryUpdate,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    existing = await memory_repository.get_memory(db, memory_id)
    if not existing or existing.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Memory not found")
    updated = await memory_repository.update_memory(
        db, memory_id, content=body.content, category=body.category, enabled=body.enabled
    )
    return updated


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    existing = await memory_repository.get_memory(db, memory_id)
    if not existing or existing.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Memory not found")
    await memory_repository.delete_memory(db, memory_id)


@router.delete("", status_code=204)
async def forget_all(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    deleted = await memory_repository.delete_all(db, profile_id)
    await audit_repository.record(
        db, user.id, "memory.forget_all", resource=profile_id,
        detail=f"deleted={deleted}", ip=_client_ip(request),
    )
