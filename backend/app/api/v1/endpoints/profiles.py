"""
GET    /v1/profiles        — list the current user's profiles
POST   /v1/profiles        — create a new profile owned by the current user
DELETE /v1/profiles/{id}   — delete one of the user's profiles and its conversations
"""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.db import audit_repository
from app.db.database import get_db
from app.db import profile_repository as repo
from app.dependencies.auth import block_read_only, get_current_user
from app.schemas.auth import UserOut
from app.schemas.profiles import Profile, ProfileCreate, ProfileUpdate

router = APIRouter()

# Phase 22: UI languages the backend accepts for a profile's persisted locale.
SUPPORTED_LOCALES = {"en", "it", "fr", "de", "es"}


@router.get("", response_model=list[Profile])
async def list_profiles(
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    return await repo.list_profiles(db, user_id=user.id)


@router.post("", response_model=Profile, status_code=201)
async def create_profile(
    body: ProfileCreate,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(block_read_only),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    return await repo.create_profile(db, name, user_id=user.id)


@router.patch("/{profile_id}", response_model=Profile)
async def update_profile(
    profile_id: str,
    body: ProfileUpdate,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    """Update a profile's preferences. Currently the UI locale (Phase 22)."""
    profile = await repo.get_profile(db, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile.user_id != user.id:
        raise HTTPException(status_code=403, detail="Profile does not belong to you")
    if body.locale is not None and body.locale not in SUPPORTED_LOCALES:
        raise HTTPException(status_code=422, detail="Unsupported locale")
    await repo.set_locale(db, profile_id, body.locale)
    return await repo.get_profile(db, profile_id)


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(block_read_only),
):
    profile = await repo.get_profile(db, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile.user_id != user.id:
        raise HTTPException(status_code=403, detail="Profile does not belong to you")
    await repo.delete_profile(db, profile_id)
    await audit_repository.record(
        db, user.id, "profile.delete", resource=profile_id,
        ip=request.client.host if request.client else None,
    )
