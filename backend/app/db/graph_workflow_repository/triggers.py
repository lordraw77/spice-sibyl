"""Triggers of every kind plus their schedule bookkeeping.

Extracted from the former single-file graph_workflow_repository.py.
"""

import json
import secrets
import uuid

import aiosqlite

from app.schemas.graph_workflows import WorkflowScheduleOut, WorkflowTriggerOut

from ._common import _now


# ── triggers ────────────────────────────────────────────────────────────────

def _row_to_trigger(row: aiosqlite.Row) -> WorkflowTriggerOut:
    return WorkflowTriggerOut(
        id=row["id"],
        workflow_id=row["workflow_id"],
        type=row["type"],
        config=json.loads(row["config_json"]),
        token=row["token"],
        next_run_at=row["next_run_at"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        fail_count=row["fail_count"] if row["fail_count"] is not None else 0,
        last_error=row["last_error"],
    )


async def create_trigger(
    db: aiosqlite.Connection,
    workflow_id: str,
    trigger_type: str,
    config: dict,
    *,
    next_run_at: int | None = None,
    enabled: bool = True,
) -> WorkflowTriggerOut:
    tr_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(24) if trigger_type == "webhook" else None
    await db.execute(
        "INSERT INTO workflow_triggers (id, workflow_id, type, config_json, token, next_run_at, enabled, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (tr_id, workflow_id, trigger_type, json.dumps(config), token, next_run_at, int(enabled), _now()),
    )
    await db.commit()
    return await get_trigger(db, tr_id)  # type: ignore[return-value]


async def get_trigger(db: aiosqlite.Connection, tr_id: str) -> WorkflowTriggerOut | None:
    async with db.execute("SELECT * FROM workflow_triggers WHERE id = ?", (tr_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_trigger(row) if row else None


async def get_trigger_by_token(db: aiosqlite.Connection, token: str) -> WorkflowTriggerOut | None:
    async with db.execute("SELECT * FROM workflow_triggers WHERE token = ?", (token,)) as cur:
        row = await cur.fetchone()
    return _row_to_trigger(row) if row else None


async def list_triggers(db: aiosqlite.Connection, workflow_id: str) -> list[WorkflowTriggerOut]:
    async with db.execute(
        "SELECT * FROM workflow_triggers WHERE workflow_id = ? ORDER BY created_at", (workflow_id,)
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_trigger(r) for r in rows]


async def list_due_schedule_triggers(db: aiosqlite.Connection, now_ts: int) -> list[dict]:
    """Enabled schedule triggers whose next_run_at has passed, joined to their
    (active) workflow so the poll loop can fire them."""
    async with db.execute(
        "SELECT t.*, w.profile_id AS wf_profile_id, w.active AS wf_active, w.blackout_json AS wf_blackout "
        "FROM workflow_triggers t JOIN workflows w ON w.id = t.workflow_id "
        "WHERE t.type = 'schedule' AND t.enabled = 1 AND w.active = 1 "
        "AND t.next_run_at IS NOT NULL AND t.next_run_at <= ?",
        (now_ts,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def list_event_triggers(db: aiosqlite.Connection, event_type: str) -> list[dict]:
    """Enabled event triggers on active workflows subscribed to ``event_type``."""
    async with db.execute(
        "SELECT t.*, w.profile_id AS wf_profile_id FROM workflow_triggers t "
        "JOIN workflows w ON w.id = t.workflow_id "
        "WHERE t.type = 'event' AND t.enabled = 1 AND w.active = 1",
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        cfg = json.loads(r["config_json"])
        if cfg.get("event") == event_type or cfg.get("event") in (None, "", "*"):
            out.append(dict(r))
    return out


async def list_error_triggers(db: aiosqlite.Connection, failed_workflow_id: str) -> list[dict]:
    """Enabled ``error`` triggers on active workflows watching ``failed_workflow_id``
    (fase 2.5). A trigger watches everything when its config has no ``workflow_id``
    (or ``""``/``"*"``); a workflow never receives its own failures (loop guard)."""
    async with db.execute(
        "SELECT t.*, w.profile_id AS wf_profile_id FROM workflow_triggers t "
        "JOIN workflows w ON w.id = t.workflow_id "
        "WHERE t.type = 'error' AND t.enabled = 1 AND w.active = 1",
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        if r["workflow_id"] == failed_workflow_id:
            continue
        cfg = json.loads(r["config_json"])
        watched = cfg.get("workflow_id")
        if watched in (None, "", "*") or watched == failed_workflow_id:
            out.append(dict(r))
    return out


async def list_success_triggers(db: aiosqlite.Connection, completed_workflow_id: str) -> list[dict]:
    """Enabled ``success`` triggers on active workflows watching
    ``completed_workflow_id`` (fase 6.1) — the exact mirror of
    :func:`list_error_triggers`, with the same wildcard and self-watch guards."""
    async with db.execute(
        "SELECT t.*, w.profile_id AS wf_profile_id FROM workflow_triggers t "
        "JOIN workflows w ON w.id = t.workflow_id "
        "WHERE t.type = 'success' AND t.enabled = 1 AND w.active = 1",
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        if r["workflow_id"] == completed_workflow_id:
            continue
        cfg = json.loads(r["config_json"])
        watched = cfg.get("workflow_id")
        if watched in (None, "", "*") or watched == completed_workflow_id:
            out.append(dict(r))
    return out


async def list_due_poll_triggers(db: aiosqlite.Connection, now_ts: int) -> list[dict]:
    """Enabled ``file.watch`` / ``email.inbound`` / ``queue.consume`` /
    ``rss.read`` triggers of active workflows whose next poll is due (fase 6.2,
    14.4, 15.4). ``next_run_at`` doubles as the next-poll timestamp; NULL means
    "never polled yet" and counts as due."""
    async with db.execute(
        "SELECT t.*, w.profile_id AS wf_profile_id "
        "FROM workflow_triggers t JOIN workflows w ON w.id = t.workflow_id "
        "WHERE t.type IN ('file.watch', 'email.inbound', 'queue.consume', 'rss.read') AND t.enabled = 1 AND w.active = 1 "
        "AND (t.next_run_at IS NULL OR t.next_run_at <= ?)",
        (now_ts,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def update_trigger_config(db: aiosqlite.Connection, tr_id: str, config: dict) -> None:
    await db.execute(
        "UPDATE workflow_triggers SET config_json = ? WHERE id = ?", (json.dumps(config), tr_id)
    )
    await db.commit()


async def set_trigger_enabled(db: aiosqlite.Connection, tr_id: str, enabled: bool) -> None:
    # Re-enabling (e.g. after an auto-disable) also clears the failure streak.
    await db.execute(
        "UPDATE workflow_triggers SET enabled = ?, fail_count = 0, last_error = NULL WHERE id = ?"
        if enabled else
        "UPDATE workflow_triggers SET enabled = ? WHERE id = ?",
        (int(enabled), tr_id),
    )
    await db.commit()


async def set_trigger_next_run(db: aiosqlite.Connection, tr_id: str, next_run_at: int | None) -> None:
    await db.execute(
        "UPDATE workflow_triggers SET next_run_at = ? WHERE id = ?", (next_run_at, tr_id)
    )
    await db.commit()


async def list_schedules_for_profile(db: aiosqlite.Connection, profile_id: str) -> list[WorkflowScheduleOut]:
    """Every trigger of every workflow owned by ``profile_id``, joined to the
    workflow's active flag and its most recent run — feeds the Phase 30.e
    schedules overview page (one row per trigger, not per workflow)."""
    async with db.execute(
        "SELECT t.*, w.name AS wf_name, w.active AS wf_active "
        "FROM workflow_triggers t JOIN workflows w ON w.id = t.workflow_id "
        "WHERE w.profile_id = ? ORDER BY w.name, t.created_at",
        (profile_id,),
    ) as cur:
        rows = await cur.fetchall()

    out: list[WorkflowScheduleOut] = []
    for row in rows:
        async with db.execute(
            "SELECT status, created_at FROM workflow_runs WHERE workflow_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (row["workflow_id"],),
        ) as cur:
            last_run = await cur.fetchone()
        out.append(WorkflowScheduleOut(
            workflow_id=row["workflow_id"],
            workflow_name=row["wf_name"],
            workflow_active=bool(row["wf_active"]),
            trigger_id=row["id"],
            trigger_type=row["type"],
            config=json.loads(row["config_json"]),
            next_run_at=row["next_run_at"],
            enabled=bool(row["enabled"]),
            fail_count=row["fail_count"] if row["fail_count"] is not None else 0,
            last_error=row["last_error"],
            last_run_status=last_run["status"] if last_run else None,
            last_run_at=last_run["created_at"] if last_run else None,
        ))
    return out


async def record_trigger_success(db: aiosqlite.Connection, tr_id: str) -> None:
    await db.execute(
        "UPDATE workflow_triggers SET fail_count = 0, last_error = NULL WHERE id = ?", (tr_id,)
    )
    await db.commit()


async def record_trigger_failure(db: aiosqlite.Connection, tr_id: str, error: str) -> int:
    """Bump the consecutive-failure streak and return the new count."""
    await db.execute(
        "UPDATE workflow_triggers SET fail_count = fail_count + 1, last_error = ? WHERE id = ?",
        (error, tr_id),
    )
    await db.commit()
    async with db.execute("SELECT fail_count FROM workflow_triggers WHERE id = ?", (tr_id,)) as cur:
        row = await cur.fetchone()
    return row["fail_count"] if row else 0


async def delete_trigger(db: aiosqlite.Connection, tr_id: str) -> bool:
    cur = await db.execute("DELETE FROM workflow_triggers WHERE id = ?", (tr_id,))
    await db.commit()
    return cur.rowcount > 0
