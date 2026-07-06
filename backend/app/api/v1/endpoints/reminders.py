"""
Cross-channel reminder management endpoints (Phase 23.d).

  GET    /v1/reminders            — list the caller's reminders
  POST   /v1/reminders            — create a reminder (structured fields)
  PATCH  /v1/reminders/{id}       — edit / pause / resume
  DELETE /v1/reminders/{id}       — cancel
  POST   /v1/reminders/{id}/snooze — snooze by N minutes (default 10)
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.dependencies.auth import resolve_profile
from app.services import reminder_service

router = APIRouter()


class ReminderOut(BaseModel):
    id: str
    text: str | None = None
    smart_prompt: str | None = None
    recurrence: str
    fire_at: int
    timezone: str | None = None
    channels: str
    active: bool
    fired: bool
    created_at: int
    last_fired_at: int | None = None

    @classmethod
    def from_row(cls, row) -> "ReminderOut":
        return cls(
            id=row["id"], text=row["text"], smart_prompt=row["smart_prompt"],
            recurrence=row["recurrence"], fire_at=row["fire_at"], timezone=row["timezone"],
            channels=row["channels"], active=bool(row["active"]), fired=bool(row["fired"]),
            created_at=row["created_at"], last_fired_at=row["last_fired_at"],
        )


class ReminderCreateIn(BaseModel):
    text: str | None = None
    smart_prompt: str | None = None
    recurrence: str = "once"
    fire_at: int
    timezone: str | None = None
    channels: str = "web"


class ReminderPatchIn(BaseModel):
    text: str | None = None
    smart_prompt: str | None = None
    recurrence: str | None = None
    fire_at: int | None = None
    timezone: str | None = None
    channels: str | None = None
    active: bool | None = None


class SnoozeIn(BaseModel):
    minutes: int = 10


@router.get("", response_model=list[ReminderOut])
async def list_reminders(profile_id: str = Depends(resolve_profile)):
    rows = await reminder_service.list_for_profile(profile_id)
    return [ReminderOut.from_row(r) for r in rows]


@router.post("", response_model=ReminderOut)
async def create_reminder(body: ReminderCreateIn, profile_id: str = Depends(resolve_profile)):
    if not body.text and not body.smart_prompt:
        raise HTTPException(status_code=400, detail="Either text or smart_prompt is required")
    reminder_id = await reminder_service.create(
        owner_profile_id=profile_id,
        text=body.text,
        smart_prompt=body.smart_prompt,
        recurrence=body.recurrence,
        fire_at=body.fire_at,
        timezone=body.timezone,
        channels=body.channels,
    )
    row = await reminder_service.get(reminder_id)
    return ReminderOut.from_row(row)


async def _owned_reminder(reminder_id: str, profile_id: str):
    row = await reminder_service.get(reminder_id)
    if not row or row["owner_profile_id"] != profile_id:
        raise HTTPException(status_code=404, detail="Reminder not found")
    return row


@router.patch("/{reminder_id}", response_model=ReminderOut)
async def patch_reminder(reminder_id: str, body: ReminderPatchIn, profile_id: str = Depends(resolve_profile)):
    await _owned_reminder(reminder_id, profile_id)
    fields = body.model_dump(exclude_unset=True)
    if "active" in fields:
        fields["active"] = 1 if fields["active"] else 0
    if fields:
        await reminder_service.edit(reminder_id, **fields)
    row = await reminder_service.get(reminder_id)
    return ReminderOut.from_row(row)


@router.delete("/{reminder_id}")
async def delete_reminder(reminder_id: str, profile_id: str = Depends(resolve_profile)):
    await _owned_reminder(reminder_id, profile_id)
    await reminder_service.delete(reminder_id, owner_profile_id=profile_id)
    return {"ok": True}


@router.post("/{reminder_id}/snooze", response_model=ReminderOut)
async def snooze_reminder(reminder_id: str, body: SnoozeIn, profile_id: str = Depends(resolve_profile)):
    await _owned_reminder(reminder_id, profile_id)
    await reminder_service.snooze(reminder_id, body.minutes)
    row = await reminder_service.get(reminder_id)
    return ReminderOut.from_row(row)


@router.post("/{reminder_id}/repeat", response_model=ReminderOut)
async def repeat_reminder(reminder_id: str, profile_id: str = Depends(resolve_profile)):
    """Re-deliver the reminder's content immediately, without touching its schedule."""
    await _owned_reminder(reminder_id, profile_id)
    await reminder_service.repeat(reminder_id)
    row = await reminder_service.get(reminder_id)
    return ReminderOut.from_row(row)
