"""
Phase 29 — graph workflow persistence.

CRUD over ``workflows`` (+ immutable ``workflow_versions``), ``workflow_runs``,
``workflow_node_runs`` and ``workflow_triggers``. The engine
(``workflow_graph_service``) drives run/node-run state; the API layer drives
workflow + trigger CRUD.
"""

import json
import secrets
import time
import uuid

import aiosqlite

from app.schemas.graph_workflows import (
    GraphRunOut,
    GraphWorkflowOut,
    NodeRunOut,
    WorkflowApprovalOut,
    WorkflowGraph,
    WorkflowScheduleOut,
    WorkflowStatsOut,
    WorkflowTriggerOut,
)


def _now() -> int:
    return int(time.time())


# ── workflows ───────────────────────────────────────────────────────────────

def _row_to_workflow(row: aiosqlite.Row) -> GraphWorkflowOut:
    try:
        variables = json.loads(row["variables_json"] or "{}")
    except (KeyError, IndexError, ValueError):
        variables = {}
    try:
        max_concurrent_runs = int(row["max_concurrent_runs"] or 0)
    except (KeyError, IndexError, ValueError, TypeError):
        max_concurrent_runs = 0
    return GraphWorkflowOut(
        id=row["id"],
        profile_id=row["profile_id"],
        name=row["name"],
        description=row["description"],
        graph=WorkflowGraph.model_validate(json.loads(row["graph_json"])),
        variables=variables if isinstance(variables, dict) else {},
        max_concurrent_runs=max_concurrent_runs,
        active=bool(row["active"]),
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def create_workflow(
    db: aiosqlite.Connection, profile_id: str, name: str, description: str, graph: WorkflowGraph,
    variables: dict | None = None, max_concurrent_runs: int = 0,
) -> GraphWorkflowOut:
    wf_id = str(uuid.uuid4())
    now = _now()
    graph_json = json.dumps(graph.model_dump())
    await db.execute(
        "INSERT INTO workflows (id, profile_id, name, description, graph_json, variables_json, max_concurrent_runs, active, version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?)",
        (wf_id, profile_id, name, description, graph_json, json.dumps(variables or {}),
         max(0, int(max_concurrent_runs or 0)), now, now),
    )
    await _snapshot_version(db, wf_id, 1, graph_json)
    await db.commit()
    return await get_workflow(db, wf_id)  # type: ignore[return-value]


async def get_workflow(db: aiosqlite.Connection, wf_id: str) -> GraphWorkflowOut | None:
    async with db.execute("SELECT * FROM workflows WHERE id = ?", (wf_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_workflow(row) if row else None


async def list_workflows(db: aiosqlite.Connection, profile_id: str) -> list[GraphWorkflowOut]:
    async with db.execute(
        "SELECT * FROM workflows WHERE profile_id = ? ORDER BY updated_at DESC", (profile_id,)
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_workflow(r) for r in rows]


async def update_workflow(
    db: aiosqlite.Connection,
    wf_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    graph: WorkflowGraph | None = None,
    active: bool | None = None,
    variables: dict | None = None,
    max_concurrent_runs: int | None = None,
) -> GraphWorkflowOut | None:
    current = await get_workflow(db, wf_id)
    if current is None:
        return None

    version = current.version
    if graph is not None:
        version += 1
        graph_json = json.dumps(graph.model_dump())
        await _snapshot_version(db, wf_id, version, graph_json)
    else:
        graph_json = None

    await db.execute(
        "UPDATE workflows SET name = COALESCE(?, name), description = COALESCE(?, description), "
        "graph_json = COALESCE(?, graph_json), variables_json = COALESCE(?, variables_json), "
        "max_concurrent_runs = COALESCE(?, max_concurrent_runs), "
        "active = COALESCE(?, active), version = ?, updated_at = ? "
        "WHERE id = ?",
        (
            name,
            description,
            graph_json,
            None if variables is None else json.dumps(variables),
            None if max_concurrent_runs is None else max(0, int(max_concurrent_runs)),
            None if active is None else int(active),
            version,
            _now(),
            wf_id,
        ),
    )
    await db.commit()
    return await get_workflow(db, wf_id)


async def set_active(db: aiosqlite.Connection, wf_id: str, active: bool) -> None:
    await db.execute(
        "UPDATE workflows SET active = ?, updated_at = ? WHERE id = ?",
        (int(active), _now(), wf_id),
    )
    await db.commit()


async def delete_workflow(db: aiosqlite.Connection, wf_id: str) -> bool:
    cur = await db.execute("DELETE FROM workflows WHERE id = ?", (wf_id,))
    await db.commit()
    return cur.rowcount > 0


# ── versions ────────────────────────────────────────────────────────────────

async def _snapshot_version(
    db: aiosqlite.Connection, wf_id: str, version: int, graph_json: str
) -> None:
    await db.execute(
        "INSERT INTO workflow_versions (id, workflow_id, version, graph_json, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), wf_id, version, graph_json, _now()),
    )


async def list_versions(db: aiosqlite.Connection, wf_id: str) -> list[dict]:
    async with db.execute(
        "SELECT version, created_at FROM workflow_versions WHERE workflow_id = ? ORDER BY version DESC",
        (wf_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [{"version": r["version"], "created_at": r["created_at"]} for r in rows]


async def get_version_graph(
    db: aiosqlite.Connection, wf_id: str, version: int
) -> WorkflowGraph | None:
    async with db.execute(
        "SELECT graph_json FROM workflow_versions WHERE workflow_id = ? AND version = ?",
        (wf_id, version),
    ) as cur:
        row = await cur.fetchone()
    return WorkflowGraph.model_validate(json.loads(row["graph_json"])) if row else None


# ── secrets (Phase 32 — roadmap fase 1) ─────────────────────────────────────
# Values arrive/leave this module already encrypted; encryption itself lives in
# vault_service so the key handling stays in one place.

async def upsert_secret(
    db: aiosqlite.Connection, profile_id: str, name: str, value_encrypted: str
) -> None:
    now = _now()
    await db.execute(
        "INSERT INTO workflow_secrets (id, profile_id, name, value_encrypted, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(profile_id, name) DO UPDATE SET value_encrypted = excluded.value_encrypted, updated_at = excluded.updated_at",
        (str(uuid.uuid4()), profile_id, name, value_encrypted, now, now),
    )
    await db.commit()


async def list_secrets(db: aiosqlite.Connection, profile_id: str) -> list[dict]:
    """Names + timestamps only — the encrypted value never leaves the DB layer
    except through :func:`get_encrypted_secrets` for the engine."""
    async with db.execute(
        "SELECT name, created_at, updated_at FROM workflow_secrets WHERE profile_id = ? ORDER BY name",
        (profile_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {"name": r["name"], "created_at": r["created_at"], "updated_at": r["updated_at"]}
        for r in rows
    ]


async def get_encrypted_secrets(db: aiosqlite.Connection, profile_id: str) -> dict[str, str]:
    async with db.execute(
        "SELECT name, value_encrypted FROM workflow_secrets WHERE profile_id = ?", (profile_id,)
    ) as cur:
        rows = await cur.fetchall()
    return {r["name"]: r["value_encrypted"] for r in rows}


async def delete_secret(db: aiosqlite.Connection, profile_id: str, name: str) -> bool:
    cur = await db.execute(
        "DELETE FROM workflow_secrets WHERE profile_id = ? AND name = ?", (profile_id, name)
    )
    await db.commit()
    return cur.rowcount > 0


# ── runs ────────────────────────────────────────────────────────────────────

def _row_to_run(row: aiosqlite.Row) -> GraphRunOut:
    return GraphRunOut(
        id=row["id"],
        workflow_id=row["workflow_id"],
        profile_id=row["profile_id"],
        status=row["status"],
        trigger_type=row["trigger_type"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def create_run(
    db: aiosqlite.Connection,
    workflow_id: str,
    profile_id: str,
    trigger_type: str,
    graph_json: str,
    *,
    status: str = "pending",
    context: dict | None = None,
) -> str:
    """``status='queued'`` + ``context={'trigger': payload}`` parks the run in the
    per-workflow queue (fase 2.3); the engine re-reads the payload on promotion."""
    run_id = str(uuid.uuid4())
    now = _now()
    await db.execute(
        "INSERT INTO workflow_runs (id, workflow_id, profile_id, status, trigger_type, graph_json, context_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, workflow_id, profile_id, status, trigger_type, graph_json,
         json.dumps(context) if context is not None else None, now, now),
    )
    await db.commit()
    return run_id


async def count_active_runs(db: aiosqlite.Connection, workflow_id: str) -> int:
    """Runs of the workflow currently holding a slot (pending or running)."""
    async with db.execute(
        "SELECT COUNT(*) AS n FROM workflow_runs WHERE workflow_id = ? AND status IN ('pending', 'running')",
        (workflow_id,),
    ) as cur:
        row = await cur.fetchone()
    return row["n"] if row else 0


async def next_queued_run(db: aiosqlite.Connection, workflow_id: str) -> GraphRunOut | None:
    """The oldest queued run of the workflow (FIFO promotion order)."""
    async with db.execute(
        "SELECT * FROM workflow_runs WHERE workflow_id = ? AND status = 'queued' "
        "ORDER BY created_at ASC, id ASC LIMIT 1",
        (workflow_id,),
    ) as cur:
        row = await cur.fetchone()
    return _row_to_run(row) if row else None


async def list_interrupted_runs(db: aiosqlite.Connection) -> list[GraphRunOut]:
    """Runs left in a non-terminal state by a crash/restart: 'running'/'pending'
    rows to resume from their checkpoint (fase 2.4), 'waiting' rows whose
    human.approval node must re-attach to its pending request (fase 4.4), plus
    'queued' rows whose promotion may now be possible (fase 2.3)."""
    async with db.execute(
        "SELECT * FROM workflow_runs WHERE status IN ('pending', 'running', 'waiting', 'queued') "
        "ORDER BY created_at ASC",
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_run(r) for r in rows]


async def get_run_graph(db: aiosqlite.Connection, run_id: str) -> str | None:
    """The graph snapshot stored with the run — resume re-executes exactly what
    the run started with, not the workflow's possibly-newer graph."""
    async with db.execute(
        "SELECT graph_json FROM workflow_runs WHERE id = ?", (run_id,)
    ) as cur:
        row = await cur.fetchone()
    return row["graph_json"] if row else None


async def first_error_node(db: aiosqlite.Connection, run_id: str) -> str | None:
    """The node_id of the first node run that ended in 'error' (fase 2.5 —
    the ``failed_node`` field of the error-trigger payload)."""
    async with db.execute(
        "SELECT node_id FROM workflow_node_runs WHERE run_id = ? AND status = 'error' "
        "ORDER BY started_at ASC, id ASC LIMIT 1",
        (run_id,),
    ) as cur:
        row = await cur.fetchone()
    return row["node_id"] if row else None


async def set_run_status(
    db: aiosqlite.Connection,
    run_id: str,
    status: str,
    *,
    context: dict | None = None,
    error: str | None = None,
) -> None:
    await db.execute(
        "UPDATE workflow_runs SET status = ?, context_json = COALESCE(?, context_json), "
        "error = ?, updated_at = ? WHERE id = ?",
        (status, json.dumps(context) if context is not None else None, error, _now(), run_id),
    )
    await db.commit()


async def get_run(db: aiosqlite.Connection, run_id: str) -> GraphRunOut | None:
    async with db.execute("SELECT * FROM workflow_runs WHERE id = ?", (run_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_run(row) if row else None


async def get_run_context(db: aiosqlite.Connection, run_id: str) -> dict | None:
    """The persisted run context ({node: {id: {output}}, trigger}) or None."""
    async with db.execute(
        "SELECT context_json FROM workflow_runs WHERE id = ?", (run_id,)
    ) as cur:
        row = await cur.fetchone()
    return json.loads(row["context_json"]) if row and row["context_json"] else None


async def get_run_status(db: aiosqlite.Connection, run_id: str) -> str | None:
    async with db.execute("SELECT status FROM workflow_runs WHERE id = ?", (run_id,)) as cur:
        row = await cur.fetchone()
    return row["status"] if row else None


async def list_runs_for_profile(
    db: aiosqlite.Connection,
    profile_id: str,
    *,
    limit: int = 100,
    status: str | None = None,
    workflow_id: str | None = None,
) -> list[GraphRunOut]:
    """Profile-wide run registry, newest first, joined to the workflow name."""
    sql = (
        "SELECT r.*, w.name AS workflow_name FROM workflow_runs r "
        "LEFT JOIN workflows w ON w.id = r.workflow_id WHERE r.profile_id = ?"
    )
    args: list = [profile_id]
    if status:
        sql += " AND r.status = ?"
        args.append(status)
    if workflow_id:
        sql += " AND r.workflow_id = ?"
        args.append(workflow_id)
    sql += " ORDER BY r.created_at DESC LIMIT ?"
    args.append(limit)
    async with db.execute(sql, args) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        run = _row_to_run(r)
        run.workflow_name = r["workflow_name"]
        out.append(run)
    return out


async def list_runs(db: aiosqlite.Connection, workflow_id: str, limit: int = 50) -> list[GraphRunOut]:
    async with db.execute(
        "SELECT * FROM workflow_runs WHERE workflow_id = ? ORDER BY created_at DESC LIMIT ?",
        (workflow_id, limit),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_run(r) for r in rows]


# ── node runs ───────────────────────────────────────────────────────────────

def _row_to_node_run(row: aiosqlite.Row) -> NodeRunOut:
    return NodeRunOut(
        id=row["id"],
        run_id=row["run_id"],
        node_id=row["node_id"],
        node_type=row["node_type"],
        status=row["status"],
        input=json.loads(row["input_json"]) if row["input_json"] else None,
        output=json.loads(row["output_json"]) if row["output_json"] else None,
        error=row["error"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


async def start_node_run(
    db: aiosqlite.Connection, run_id: str, node_id: str, node_type: str, input_data
) -> str:
    nr_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO workflow_node_runs (id, run_id, node_id, node_type, status, input_json, started_at) "
        "VALUES (?, ?, ?, ?, 'running', ?, ?)",
        (nr_id, run_id, node_id, node_type, json.dumps(input_data, default=str), _now()),
    )
    await db.commit()
    return nr_id


async def finish_node_run(
    db: aiosqlite.Connection,
    nr_id: str,
    status: str,
    *,
    output=None,
    error: str | None = None,
) -> None:
    await db.execute(
        "UPDATE workflow_node_runs SET status = ?, output_json = ?, error = ?, finished_at = ? WHERE id = ?",
        (status, json.dumps(output, default=str) if output is not None else None, error, _now(), nr_id),
    )
    await db.commit()


async def fail_running_node_runs(db: aiosqlite.Connection, run_id: str, error: str) -> None:
    """Close node runs left 'running' by a crash — resume (fase 2.4) re-executes
    those nodes, so the orphan rows are settled as errors instead of dangling."""
    await db.execute(
        "UPDATE workflow_node_runs SET status = 'error', error = ?, finished_at = ? "
        "WHERE run_id = ? AND status = 'running'",
        (error, _now(), run_id),
    )
    await db.commit()


async def record_skipped_node(
    db: aiosqlite.Connection, run_id: str, node_id: str, node_type: str
) -> None:
    now = _now()
    await db.execute(
        "INSERT INTO workflow_node_runs (id, run_id, node_id, node_type, status, started_at, finished_at) "
        "VALUES (?, ?, ?, ?, 'skipped', ?, ?)",
        (str(uuid.uuid4()), run_id, node_id, node_type, now, now),
    )
    await db.commit()


async def latest_node_outputs(db: aiosqlite.Connection, workflow_id: str) -> dict[str, dict]:
    """The most recent persisted output of every node across ALL runs of the
    workflow — powers the edge inspector when the editor is (re)opened, so
    arrow selection can show data from past executions, not just the live one.

    Returns ``{node_id: {output, run_id, finished_at, run_created_at}}``.
    """
    async with db.execute(
        "SELECT nr.node_id, nr.output_json, nr.run_id, nr.finished_at, r.created_at AS run_created_at "
        "FROM workflow_node_runs nr JOIN workflow_runs r ON r.id = nr.run_id "
        "WHERE r.workflow_id = ? AND nr.output_json IS NOT NULL "
        "ORDER BY nr.finished_at ASC, nr.started_at ASC",
        (workflow_id,),
    ) as cur:
        rows = await cur.fetchall()
    out: dict[str, dict] = {}
    for r in rows:  # ascending order → the last write per node wins
        out[r["node_id"]] = {
            "output": json.loads(r["output_json"]),
            "run_id": r["run_id"],
            "finished_at": r["finished_at"],
            "run_created_at": r["run_created_at"],
        }
    return out


async def list_node_runs(db: aiosqlite.Connection, run_id: str) -> list[NodeRunOut]:
    async with db.execute(
        "SELECT * FROM workflow_node_runs WHERE run_id = ? ORDER BY started_at, id", (run_id,)
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_node_run(r) for r in rows]


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
        "SELECT t.*, w.profile_id AS wf_profile_id, w.active AS wf_active "
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


# ── stats (Phase 36 — roadmap fase 5.1) ─────────────────────────────────────

async def workflow_stats_for_profile(db: aiosqlite.Connection, profile_id: str) -> list[WorkflowStatsOut]:
    """Per-workflow aggregates: run counts by outcome, success rate over terminal
    runs, average terminal-run duration, and the LLM token totals summed from the
    `_usage` key that llm.* node outputs carry (json_extract over output_json)."""
    async with db.execute(
        """
        SELECT w.id, w.name, w.active,
               COUNT(r.id)                                            AS runs,
               SUM(CASE WHEN r.status = 'completed' THEN 1 ELSE 0 END) AS completed,
               SUM(CASE WHEN r.status = 'failed'    THEN 1 ELSE 0 END) AS failed,
               SUM(CASE WHEN r.status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
               AVG(CASE WHEN r.status IN ('completed', 'failed')
                        THEN r.updated_at - r.created_at END)          AS avg_duration_s,
               MAX(r.created_at)                                       AS last_run_at
        FROM workflows w
        LEFT JOIN workflow_runs r ON r.workflow_id = w.id
        WHERE w.profile_id = ?
        GROUP BY w.id
        ORDER BY w.updated_at DESC
        """,
        (profile_id,),
    ) as cur:
        rows = await cur.fetchall()

    async with db.execute(
        """
        SELECT r.workflow_id,
               SUM(COALESCE(json_extract(nr.output_json, '$._usage.tokens_in'), 0))    AS tokens_in,
               SUM(COALESCE(json_extract(nr.output_json, '$._usage.tokens_out'), 0))   AS tokens_out,
               SUM(COALESCE(json_extract(nr.output_json, '$._usage.tokens_total'), 0)) AS tokens_total
        FROM workflow_node_runs nr
        JOIN workflow_runs r ON r.id = nr.run_id
        JOIN workflows w ON w.id = r.workflow_id
        WHERE w.profile_id = ? AND nr.output_json LIKE '%_usage%'
        GROUP BY r.workflow_id
        """,
        (profile_id,),
    ) as cur:
        tokens = {t["workflow_id"]: t for t in await cur.fetchall()}

    out: list[WorkflowStatsOut] = []
    for row in rows:
        completed = row["completed"] or 0
        failed = row["failed"] or 0
        terminal = completed + failed
        tok = tokens.get(row["id"])
        out.append(WorkflowStatsOut(
            workflow_id=row["id"],
            workflow_name=row["name"],
            active=bool(row["active"]),
            runs=row["runs"] or 0,
            completed=completed,
            failed=failed,
            cancelled=row["cancelled"] or 0,
            success_rate=(completed / terminal) if terminal else None,
            avg_duration_s=row["avg_duration_s"],
            tokens_in=int(tok["tokens_in"] or 0) if tok else 0,
            tokens_out=int(tok["tokens_out"] or 0) if tok else 0,
            tokens_total=int(tok["tokens_total"] or 0) if tok else 0,
            last_run_at=row["last_run_at"],
        ))
    return out


# ── approvals (Phase 35 — roadmap fase 4.4) ─────────────────────────────────

def _row_to_approval(row: aiosqlite.Row) -> WorkflowApprovalOut:
    approval = WorkflowApprovalOut(
        id=row["id"],
        run_id=row["run_id"],
        node_id=row["node_id"],
        workflow_id=row["workflow_id"],
        profile_id=row["profile_id"],
        title=row["title"],
        message=row["message"],
        status=row["status"],
        timeout_at=row["timeout_at"],
        comment=row["comment"],
        decided_by=row["decided_by"],
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
) -> WorkflowApprovalOut:
    ap_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO workflow_approvals (id, run_id, node_id, workflow_id, profile_id, title, message, status, timeout_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        (ap_id, run_id, node_id, workflow_id, profile_id, title, message, timeout_at, _now()),
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


async def list_approvals(
    db: aiosqlite.Connection,
    profile_id: str,
    *,
    status: str | None = "pending",
    run_id: str | None = None,
    limit: int = 100,
) -> list[WorkflowApprovalOut]:
    """Approval requests of the profile (newest first), joined to the workflow
    name — feeds the pending-approvals view and the run detail panel."""
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
) -> bool:
    """Settle a pending request (approved|rejected|expired|cancelled). Returns
    False when it was already decided — the engine's poll and the API can race,
    and the first writer must win."""
    cur = await db.execute(
        "UPDATE workflow_approvals SET status = ?, decided_by = ?, comment = ?, decided_at = ? "
        "WHERE id = ? AND status = 'pending'",
        (status, decided_by, comment, _now(), ap_id),
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
