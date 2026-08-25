"""Triggers and schedules, including the public webhook receiver.

Extracted verbatim from the former single-file graph_workflows.py.
"""

import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite
from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import settings
from app.db import graph_workflow_repository as repo
from app.db.database import get_db
from app.dependencies.auth import resolve_profile
from app.schemas.graph_workflows import (
    WorkflowScheduleOut,
    WorkflowTriggerCreate,
    WorkflowTriggerOut,
)
from app.services import reminder_parsing, workflow_graph_service as engine

from ._common import _owned

router = APIRouter()
public_router = APIRouter()

@router.get("/schedules", response_model=list[WorkflowScheduleOut])
async def list_schedules(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Cross-workflow schedules overview (Phase 30.e): every trigger of every
    workflow owned by the profile, with its next run and last run status —
    static path declared before ``/{wf_id}`` so it isn't swallowed by it."""
    return await repo.list_schedules_for_profile(db, profile_id)


# ── triggers ────────────────────────────────────────────────────────────────

@router.get("/{wf_id}/triggers", response_model=list[WorkflowTriggerOut])
async def list_triggers(
    wf_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await _owned(db, wf_id, profile_id)
    return await repo.list_triggers(db, wf_id)


@router.post("/{wf_id}/triggers", response_model=WorkflowTriggerOut, status_code=201)
async def create_trigger(
    wf_id: str,
    body: WorkflowTriggerCreate,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await _owned(db, wf_id, profile_id)
    next_run_at = None
    config = dict(body.config)
    if body.type == "schedule":
        next_run_at, config = _resolve_schedule(config)
    elif body.type in ("file.watch", "email.inbound", "rss.read"):
        # Fase 6.2 / 15.4 — poll-based triggers: next_run_at doubles as the
        # next-poll timestamp; leaving it NULL makes the first poll happen right away.
        if body.type == "email.inbound" and not str(config.get("host") or "").strip():
            raise HTTPException(status_code=400, detail="email.inbound trigger needs a 'host' (IMAP server)")
        if body.type == "rss.read" and not str(config.get("url") or "").strip().startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="rss.read trigger needs a 'url' (RSS/Atom feed http(s) URL)")
    trigger = await repo.create_trigger(
        db, wf_id, body.type, config, next_run_at=next_run_at, enabled=body.enabled
    )
    return trigger


_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse_hhmm(value: str | None) -> tuple[int, int]:
    m = _HHMM_RE.match(str(value or "").strip())
    if not m:
        raise HTTPException(status_code=400, detail="'time' must be HH:MM")
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        raise HTTPException(status_code=400, detail="'time' must be HH:MM")
    return hour, minute


def _next_weekday_at(now: datetime, weekday: str, hour: int, minute: int) -> datetime:
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (_WEEKDAYS.index(weekday) - now.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def _resolve_schedule(config: dict) -> tuple[int | None, dict]:
    """Normalise a schedule trigger config into ``recurrence`` + ``next_run_at``.

    Phase 30.f — the Schedules page builds a structured ``pattern``
    (daily|weekly|cron|once) instead of free natural language, so the picked
    day/time or cron expression is honoured exactly (the old ``text``/
    ``recurrence`` fields — used by the designer's quick-add and the API —
    still work unchanged for backward compatibility).
    """
    tz = ZoneInfo(getattr(settings, "timezone", None) or "UTC")
    now = datetime.now(tz)
    pattern = config.get("pattern")

    if pattern == "daily":
        hour, minute = _parse_hhmm(config.get("time"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return int(target.timestamp()), {**config, "recurrence": "daily"}

    if pattern == "weekly":
        hour, minute = _parse_hhmm(config.get("time"))
        weekdays = [d for d in (config.get("weekdays") or []) if d in _WEEKDAYS]
        if not weekdays:
            raise HTTPException(status_code=400, detail="'weekdays' must include at least one day")
        candidates = [_next_weekday_at(now, d, hour, minute) for d in weekdays]
        recurrence = "weekly:" + ",".join(weekdays)
        return int(min(candidates).timestamp()), {**config, "recurrence": recurrence}

    if pattern == "cron":
        # Fase 6.1 — a schedule may carry MULTIPLE cron expressions ('crons'
        # list, or 'cron' as a list) for mixed timetables; a single 'cron'
        # string keeps the original behaviour and encoding.
        raw = config.get("crons") if config.get("crons") not in (None, "", []) else config.get("cron")
        exprs = [str(e).strip() for e in (raw if isinstance(raw, list) else [raw]) if str(e or "").strip()]
        if not exprs:
            raise HTTPException(status_code=400, detail="'cron' (or 'crons') is required")
        firsts: list[datetime] = []
        for expr in exprs:
            fields = expr.split()
            if len(fields) != 5:
                raise HTTPException(status_code=400, detail=f"'{expr}': cron must have 5 space-separated fields")
            try:
                firsts.append(croniter(expr, now).get_next(datetime))
            except (ValueError, KeyError) as exc:
                raise HTTPException(status_code=400, detail=f"invalid cron expression '{expr}': {exc}") from None
        if len(exprs) == 1:
            recurrence = "cron:" + ",".join(exprs[0].split())
        else:
            recurrence = "crons:" + "|".join(",".join(e.split()) for e in exprs)
        return int(min(firsts).timestamp()), {**config, "recurrence": recurrence}

    if pattern == "once":
        hour, minute = _parse_hhmm(config.get("time"))
        date_str = config.get("date")
        if date_str:
            try:
                target = datetime.strptime(str(date_str), "%Y-%m-%d").replace(
                    hour=hour, minute=minute, second=0, microsecond=0, tzinfo=tz,
                )
            except ValueError:
                raise HTTPException(status_code=400, detail="'date' must be YYYY-MM-DD") from None
        else:
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
        return int(target.timestamp()), {**config, "recurrence": "once"}

    # Legacy fallback: natural-language `text` (designer's quick-add) or an
    # explicit compact `recurrence` string, as accepted since Phase 29.b.
    text = config.get("text")
    if text:
        parsed = reminder_parsing.parse_recurrence_and_when(str(text), tz)
        if parsed:
            recurrence, fire_at, _ = parsed
            return fire_at, {**config, "recurrence": recurrence}
    recurrence = config.get("recurrence", "daily")
    nxt = reminder_parsing.compute_next_fire(recurrence, int(time.time()), tz)
    return nxt, {**config, "recurrence": recurrence}


@router.post("/triggers/{tid}/enable", response_model=WorkflowTriggerOut)
async def enable_trigger(
    tid: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    trigger = await _owned_trigger(db, tid, profile_id)
    await repo.set_trigger_enabled(db, tid, True)
    return await repo.get_trigger(db, tid)


@router.post("/triggers/{tid}/disable", response_model=WorkflowTriggerOut)
async def disable_trigger(
    tid: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await _owned_trigger(db, tid, profile_id)
    await repo.set_trigger_enabled(db, tid, False)
    return await repo.get_trigger(db, tid)


@router.delete("/triggers/{tid}", status_code=204)
async def delete_trigger(
    tid: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await _owned_trigger(db, tid, profile_id)
    await repo.delete_trigger(db, tid)


@router.post("/triggers/{tid}/rotate-secret")
async def rotate_webhook_secret(
    tid: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Generate (or replace) the HMAC secret for a webhook trigger. Returned only
    once here — the caller must copy it into whatever sends the webhook (an
    `X-Signature: sha256=<hex hmac of the raw body>` header). Clearing signature
    enforcement is done by calling this with an empty body (no secret stored)."""
    trigger = await _owned_trigger(db, tid, profile_id)
    if trigger.type != "webhook":
        raise HTTPException(status_code=400, detail="Not a webhook trigger")
    secret = secrets.token_urlsafe(32)
    config = {**trigger.config, "secret": secret}
    await repo.update_trigger_config(db, tid, config)
    return {"secret": secret}


async def _owned_trigger(db: aiosqlite.Connection, tid: str, profile_id: str) -> WorkflowTriggerOut:
    trigger = await repo.get_trigger(db, tid)
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")
    wf = await repo.get_workflow(db, trigger.workflow_id)
    if not wf or wf.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return trigger


# ── public webhook receiver ─────────────────────────────────────────────────

@public_router.post("/hooks/{token}")
async def webhook(token: str, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """Public token-scoped webhook. Fires the workflow if its trigger is enabled
    and the workflow is active. The JSON body becomes ``$trigger``.

    If the trigger's config has a ``secret`` (set via the rotate-secret endpoint),
    the request must carry a matching ``X-Signature: sha256=<hex hmac of the raw
    body>`` header — a missing/incorrect signature is rejected before the body is
    ever parsed or the workflow runs."""
    trigger = await repo.get_trigger_by_token(db, token)
    if not trigger or not trigger.enabled:
        raise HTTPException(status_code=404, detail="Unknown webhook")
    wf = await repo.get_workflow(db, trigger.workflow_id)
    if not wf or not wf.active:
        raise HTTPException(status_code=404, detail="Workflow not active")

    raw_body = await request.body()
    secret = trigger.config.get("secret")
    if secret:
        signature = request.headers.get("x-signature", "")
        expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(raw_body) if raw_body else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"body": payload}
    try:
        # Fase 16.2 — idempotency: a `dedupKey` on the trigger dedupes repeated
        # deliveries within its window (returns the original run). Fase 7.2 —
        # environment; fase 16.4 — priority; both read from the trigger config.
        run_id, deduped = await engine.run_from_trigger(db, trigger, wf, "webhook", payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run_id": run_id, "deduped": deduped}
