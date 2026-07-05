"""
Roaming preferences (Phase 23).

  GET /v1/settings/user               — the caller's UI/appearance settings blob
  PUT /v1/settings/user               — persist the caller's UI/appearance blob
  GET /v1/settings/profile/{id}       — a profile's chat settings blob
  PUT /v1/settings/profile/{id}       — persist a profile's chat settings blob

The blob shape is owned by the frontend. Read-only accounts are blocked on the
PUTs by the router-level block_read_only dependency (mutating method).
"""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from app.db.database import get_db
from app.db import profile_repository
from app.db import settings_repository as repo
from app.dependencies.auth import get_current_user
from app.schemas.auth import UserOut
from app.schemas.settings import SettingsBlob

router = APIRouter()


@router.get("/user", response_model=SettingsBlob)
async def get_user_settings(
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    return SettingsBlob(data=await repo.get(db, f"user:{user.id}"))


@router.put("/user", response_model=SettingsBlob)
async def put_user_settings(
    body: SettingsBlob,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await repo.put(db, f"user:{user.id}", body.data)
    return body


async def _owned_profile(
    db: aiosqlite.Connection, profile_id: str, user: UserOut
) -> None:
    profile = await profile_repository.get_profile(db, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile.user_id != user.id:
        raise HTTPException(status_code=403, detail="Profile does not belong to you")


@router.get("/profile/{profile_id}", response_model=SettingsBlob)
async def get_profile_settings(
    profile_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _owned_profile(db, profile_id, user)
    return SettingsBlob(data=await repo.get(db, f"profile:{profile_id}"))


@router.put("/profile/{profile_id}", response_model=SettingsBlob)
async def put_profile_settings(
    profile_id: str,
    body: SettingsBlob,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _owned_profile(db, profile_id, user)
    await repo.put(db, f"profile:{profile_id}", body.data)
    return body
