"""Human-in-the-loop requests: approvals, inputs and awaited events.

Extracted from the former single-file graph_workflow_repository.py.
"""

import json
import uuid

import aiosqlite

from app.schemas.graph_workflows import WorkflowApprovalOut

from ._common import _now


# ── approvals (Phase 35 — roadmap fase 4.4; generalised Phase 42 — fase 10) ──

def _row_to_approval(row: aiosqlite.Row) -> WorkflowApprovalOut:
    schema_json = row["schema_json"]
    data_json = row["data_json"]
    approval = WorkflowApprovalOut(
        id=row["id"],
        run_id=row["run_id"],
        node_id=row["node_id"],
        workflow_id=row["workflow_id"],
        profile_id=row["profile_id"],
        kind=row["kind"] or "approval",
        title=row["title"],
        message=row["message"],
        status=row["status"],
        timeout_at=row["timeout_at"],
        comment=row["comment"],
        decided_by=row["decided_by"],
        form_schema=json.loads(schema_json) if schema_json else None,
        data=json.loads(data_json) if data_json else None,
        correlation_id=row["correlation_id"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
    )
    try:
        approval.workflow_name = row["workflow_name"]
    except (KeyError, IndexError):
        pass
    return approval


async def create_approval(
    db: aiosqlite.Connection,
    run_id: str,
    node_id: str,
    workflow_id: str,
    profile_id: str,
    *,
    title: str,
    message: str,
    timeout_at: int | None,
    kind: str = "approval",
    schema: dict | None = None,
    correlation_id: str | None = None,
) -> WorkflowApprovalOut:
    ap_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO workflow_approvals "
        "(id, run_id, node_id, workflow_id, profile_id, title, message, status, timeout_at, created_at, kind, schema_json, correlation_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
        (
            ap_id, run_id, node_id, workflow_id, profile_id, title, message, timeout_at, _now(),
            kind, json.dumps(schema) if schema is not None else None, correlation_id,
        ),
    )
    await db.commit()
    return await get_approval(db, ap_id)  # type: ignore[return-value]


async def get_approval(db: aiosqlite.Connection, ap_id: str) -> WorkflowApprovalOut | None:
    async with db.execute("SELECT * FROM workflow_approvals WHERE id = ?", (ap_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_approval(row) if row else None


async def get_pending_approval(
    db: aiosqlite.Connection, run_id: str, node_id: str
) -> WorkflowApprovalOut | None:
    """The pending request of a run's approval node — lets a resumed run
    re-attach to the request it created before the restart."""
    async with db.execute(
        "SELECT * FROM workflow_approvals WHERE run_id = ? AND node_id = ? AND status = 'pending' "
        "ORDER BY created_at DESC LIMIT 1",
        (run_id, node_id),
    ) as cur:
        row = await cur.fetchone()
    return _row_to_approval(row) if row else None


async def get_pending_event(
    db: aiosqlite.Connection, correlation_id: str, profile_id: str
) -> WorkflowApprovalOut | None:
    """The pending wait.event request matching a correlation id, scoped to the
    caller's profile (Phase 42 — fase 10.2). POST /events/{correlation_id} uses
    this to find what to deliver the payload to."""
    async with db.execute(
        "SELECT * FROM workflow_approvals WHERE correlation_id = ? AND profile_id = ? "
        "AND kind = 'event' AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
        (correlation_id, profile_id),
    ) as cur:
        row = await cur.fetchone()
    return _row_to_approval(row) if row else None


async def list_approvals(
    db: aiosqlite.Connection,
    profile_id: str,
    *,
    status: str | None = "pending",
    run_id: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> list[WorkflowApprovalOut]:
    """Approval requests of the profile (newest first), joined to the workflow
    name — feeds the pending-approvals view and the run detail panel. ``kind``
    (Phase 42) scopes to approval|input|event; omitted returns every kind."""
    sql = (
        "SELECT a.*, w.name AS workflow_name FROM workflow_approvals a "
        "LEFT JOIN workflows w ON w.id = a.workflow_id WHERE a.profile_id = ?"
    )
    args: list = [profile_id]
    if status:
        sql += " AND a.status = ?"
        args.append(status)
    if run_id:
        sql += " AND a.run_id = ?"
        args.append(run_id)
    if kind:
        sql += " AND a.kind = ?"
        args.append(kind)
    sql += " ORDER BY a.created_at DESC LIMIT ?"
    args.append(limit)
    async with db.execute(sql, args) as cur:
        rows = await cur.fetchall()
    return [_row_to_approval(r) for r in rows]


async def decide_approval(
    db: aiosqlite.Connection,
    ap_id: str,
    *,
    status: str,
    decided_by: str | None = None,
    comment: str | None = None,
    data: dict | list | str | int | float | bool | None = None,
) -> bool:
    """Settle a pending request (approved|rejected|expired|cancelled, plus
    submitted|delivered for human.input/wait.event — Phase 42). Returns False
    when it was already decided — the engine's poll and the API can race, and
    the first writer must win. ``data`` (when given) persists the submitted
    form / delivered event payload alongside the decision."""
    cur = await db.execute(
        "UPDATE workflow_approvals SET status = ?, decided_by = ?, comment = ?, decided_at = ?, "
        "data_json = COALESCE(?, data_json) WHERE id = ? AND status = 'pending'",
        (status, decided_by, comment, _now(), json.dumps(data) if data is not None else None, ap_id),
    )
    await db.commit()
    return cur.rowcount > 0


async def cancel_pending_approvals(db: aiosqlite.Connection, run_id: str) -> None:
    """Settle every pending request of a run as 'cancelled' — called when the
    run itself is cancelled or fails, so no orphan requests linger."""
    await db.execute(
        "UPDATE workflow_approvals SET status = 'cancelled', decided_at = ? "
        "WHERE run_id = ? AND status = 'pending'",
        (_now(), run_id),
    )
    await db.commit()
