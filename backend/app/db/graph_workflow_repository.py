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
    WorkflowGraph,
    WorkflowScheduleOut,
    WorkflowTriggerOut,
)


def _now() -> int:
    return int(time.time())


# ── workflows ───────────────────────────────────────────────────────────────

def _row_to_workflow(row: aiosqlite.Row) -> GraphWorkflowOut:
    return GraphWorkflowOut(
        id=row["id"],
        profile_id=row["profile_id"],
        name=row["name"],
        description=row["description"],
        graph=WorkflowGraph.model_validate(json.loads(row["graph_json"])),
        active=bool(row["active"]),
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def create_workflow(
    db: aiosqlite.Connection, profile_id: str, name: str, description: str, graph: WorkflowGraph
) -> GraphWorkflowOut:
    wf_id = str(uuid.uuid4())
    now = _now()
    graph_json = json.dumps(graph.model_dump())
    await db.execute(
        "INSERT INTO workflows (id, profile_id, name, description, graph_json, active, version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 0, 1, ?, ?)",
        (wf_id, profile_id, name, description, graph_json, now, now),
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
        "graph_json = COALESCE(?, graph_json), active = COALESCE(?, active), version = ?, updated_at = ? "
        "WHERE id = ?",
        (
            name,
            description,
            graph_json,
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
) -> str:
    run_id = str(uuid.uuid4())
    now = _now()
    await db.execute(
        "INSERT INTO workflow_runs (id, workflow_id, profile_id, status, trigger_type, graph_json, created_at, updated_at) "
        "VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)",
        (run_id, workflow_id, profile_id, trigger_type, graph_json, now, now),
    )
    await db.commit()
    return run_id


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
