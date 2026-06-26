import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import telegram_link_repository as repo
from app.db.database import get_db

router = APIRouter()

_link_codes: dict[str, dict] = {}


def register_link_code(code: str, telegram_id: int, username: str | None) -> None:
    import time
    _link_codes[code] = {"telegram_id": telegram_id, "username": username, "expires": time.time() + 300}


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
    db: aiosqlite.Connection = Depends(get_db),
):
    import time
    entry = _link_codes.pop(body.code, None)
    if not entry or entry["expires"] < time.time():
        raise HTTPException(status_code=400, detail="Codice non valido o scaduto.")
    await repo.link(db, entry["telegram_id"], body.profile_id, entry.get("username"))
    return LinkStatus(linked=True, telegram_id=entry["telegram_id"], username=entry.get("username"), linked_at=int(time.time()))


@router.delete("/link/{profile_id}", status_code=204)
async def unlink_telegram(
    profile_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    await repo.unlink_by_profile(db, profile_id)


@router.get("/link/{profile_id}", response_model=LinkStatus)
async def get_link_status(
    profile_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    row = await repo.get_by_profile_id(db, profile_id)
    if not row:
        return LinkStatus(linked=False)
    return LinkStatus(linked=True, telegram_id=row["telegram_id"], username=row["username"], linked_at=row["linked_at"])
