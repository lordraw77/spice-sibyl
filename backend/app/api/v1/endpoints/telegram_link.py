"""
Telegram account linking endpoints (all under /v1/telegram).

Every route is profile-scoped: the profile_id supplied by the caller (path or
body) is checked against the authenticated user via `_owned_profile` before any
read or write.  Without that check the UUID alone would be enough to read,
hijack or unlink somebody else's Telegram link (audit finding 2.1).
"""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import profile_repository, telegram_link_repository as repo
from app.db.database import get_db
from app.dependencies.auth import get_current_user
from app.schemas.auth import UserOut

router = APIRouter()

_link_codes: dict[str, dict] = {}


def register_link_code(code: str, telegram_id: int, username: str | None) -> None:
    import time
    _link_codes[code] = {"telegram_id": telegram_id, "username": username, "expires": time.time() + 300}


async def assert_owns_profile(
    profile_id: str,
    user: UserOut,
    db: aiosqlite.Connection,
) -> str:
    """Return profile_id once proven to belong to `user`, else 403/404."""
    profile = await profile_repository.get_profile(db, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile.user_id != user.id:
        raise HTTPException(status_code=403, detail="Profile does not belong to you")
    return profile_id


async def _owned_profile(
    profile_id: str,
    user: UserOut = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> str:
    """Path-parameter flavour of the ownership check, usable as a dependency."""
    return await assert_owns_profile(profile_id, user, db)


class LinkRequest(BaseModel):
    code: str
    profile_id: str


class LinkStatus(BaseModel):
    linked: bool
    telegram_id: int | None = None
    username: str | None = None
    linked_at: int | None = None


@router.post("/link", response_model=LinkStatus)
async def link_telegram(
    body: LinkRequest,
    user: UserOut = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    import time
    await assert_owns_profile(body.profile_id, user, db)
    entry = _link_codes.pop(body.code, None)
    if not entry or entry["expires"] < time.time():
        raise HTTPException(status_code=400, detail="Codice non valido o scaduto.")
    await repo.link(db, entry["telegram_id"], body.profile_id, entry.get("username"))
    return LinkStatus(linked=True, telegram_id=entry["telegram_id"], username=entry.get("username"), linked_at=int(time.time()))


@router.delete("/link/{profile_id}", status_code=204)
async def unlink_telegram(
    profile_id: str = Depends(_owned_profile),
    db: aiosqlite.Connection = Depends(get_db),
):
    await repo.unlink_by_profile(db, profile_id)


@router.get("/link/{profile_id}", response_model=LinkStatus)
async def get_link_status(
    profile_id: str = Depends(_owned_profile),
    db: aiosqlite.Connection = Depends(get_db),
):
    row = await repo.get_by_profile_id(db, profile_id)
    if not row:
        return LinkStatus(linked=False)
    return LinkStatus(linked=True, telegram_id=row["telegram_id"], username=row["username"], linked_at=row["linked_at"])
