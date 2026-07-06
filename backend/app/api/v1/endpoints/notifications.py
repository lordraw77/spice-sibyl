"""
Cross-channel notification endpoints (Phase 23.c).

  GET  /v1/notifications/stream    — SSE stream of live Telegram → web events
  GET  /v1/notifications           — recent events + unread count (badge on load)
  POST /v1/notifications/{id}/read — mark one event read
  POST /v1/notifications/trigger   — client-observed event the backend can't see
                                      (e.g. a long completion finished while the
                                      tab was hidden), forwarded to Telegram
"""

import asyncio
import json
import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.db import notification_events_repository as events_repo
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.schemas.auth import UserOut
from app.services import notification_service

router = APIRouter()
logger = logging.getLogger(__name__)


class NotificationOut(BaseModel):
    id: str
    event_type: str
    title: str
    body: str
    meta: dict | None = None
    created_at: int
    read: bool


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    unread_count: int


class TriggerIn(BaseModel):
    event_type: str
    title: str
    body: str = ""


@router.get("", response_model=NotificationListOut)
async def list_notifications(
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    items = await events_repo.list_for_user(db, user.id)
    unread = await events_repo.unread_count(db, user.id)
    return NotificationListOut(items=[NotificationOut(**i) for i in items], unread_count=unread)


@router.post("/{event_id}/read")
async def mark_read(
    event_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    ok = await events_repo.mark_read(db, user.id, event_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"ok": True}


@router.post("/trigger")
async def trigger(
    body: TriggerIn,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Forward a client-observed event (backend can't see it) to Telegram."""
    await notification_service.notify_telegram(db, profile_id, body.event_type, f"{body.title}\n{body.body}".strip())
    return {"ok": True}


@router.get("/stream")
async def stream(
    request: Request,
    user: UserOut = Depends(get_current_user),
):
    """Fetch-based SSE stream (mirrors the chat completion stream) of live
    Telegram → web events for the authenticated user."""
    queue = notification_service.subscribe(user.id)

    async def _events():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
                    continue
                yield {"event": "notification", "data": json.dumps(event)}
        finally:
            notification_service.unsubscribe(user.id, queue)

    return EventSourceResponse(_events())
