"""
GET    /v1/profiles        — list all profiles
POST   /v1/profiles        — create a new profile
DELETE /v1/profiles/{id}   — delete profile and all its conversations
"""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from app.db.database import get_db
from app.db import profile_repository as repo
from app.schemas.profiles import Profile, ProfileCreate

router = APIRouter()


@router.get("", response_model=list[Profile])
async def list_profiles(db: aiosqlite.Connection = Depends(get_db)):
    return await repo.list_profiles(db)


@router.post("", response_model=Profile, status_code=201)
async def create_profile(
    body: ProfileCreate,
    db: aiosqlite.Connection = Depends(get_db),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    return await repo.create_profile(db, name)


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    profile = await repo.get_profile(db, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    await repo.delete_profile(db, profile_id)
