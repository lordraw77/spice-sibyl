"""Runs and node runs: creation, status, lease, step debug, node outputs.

Extracted from the former single-file graph_workflow_repository.py.
"""

import json
import uuid

import aiosqlite

from app.schemas.graph_workflows import GraphRunOut, NodeRunOut

from ._common import _now


# ── runs ────────────────────────────────────────────────────────────────────

def _parse_debug(raw) -> dict | None:
    """The run's step-debug state, exposing only what the UI needs (breakpoints,
    pending_node) — never the transient input override."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return {"breakpoints": data.get("breakpoints") or [], "pending_node": data.get("pending_node")}


def _row_to_run(row: aiosqlite.Row) -> GraphRunOut:
    def _opt(column: str):
        try:
            return row[column]
        except (KeyError, IndexError):
            return None

    return GraphRunOut(
        id=row["id"],
        workflow_id=row["workflow_id"],
        profile_id=row["profile_id"],
        status=row["status"],
        trigger_type=row["trigger_type"],
        environment=_opt("environment"),
        origin_run_id=_opt("origin_run_id"),
        debug=_parse_debug(_opt("debug_json")),
        priority=_opt("priority") or 0,
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
    environment: str | None = None,
    origin_run_id: str | None = None,
    priority: int = 0,
) -> str:
    """``status='queued'`` + ``context={'trigger': payload}`` parks the run in the
    per-workflow queue (fase 2.3); the engine re-reads the payload on promotion.
    ``environment`` records the fase 7.2 environment; ``origin_run_id`` the run
    this one was retried/replayed from (fase 7.1). ``priority`` (fase 16.4) orders
    queue promotion — higher first, FIFO within the same priority."""
    run_id = str(uuid.uuid4())
    now = _now()
    await db.execute(
        "INSERT INTO workflow_runs (id, workflow_id, profile_id, status, trigger_type, graph_json, context_json, environment, origin_run_id, priority, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, workflow_id, profile_id, status, trigger_type, graph_json,
         json.dumps(context) if context is not None else None,
         environment, origin_run_id, int(priority or 0), now, now),
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
    """The next queued run of the workflow to promote: highest ``priority`` first
    (fase 16.4), FIFO (oldest ``created_at``) within the same priority."""
    async with db.execute(
        "SELECT * FROM workflow_runs WHERE workflow_id = ? AND status = 'queued' "
        "ORDER BY priority DESC, created_at ASC, id ASC LIMIT 1",
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


# ── run lease (Phase 46 — roadmap fase 14.3, engine scale-out) ─────────────

async def acquire_lease(db: aiosqlite.Connection, run_id: str, owner: str, ttl_seconds: int) -> bool:
    """Claim the run for ``owner`` (this process instance) until now+ttl. Wins
    when unleased, already owned by ``owner`` (renewal) or the existing lease
    has expired — a stale lease from a crashed instance is simply overwritten.
    Generic conditional UPDATE: correct as a real mutual-exclusion lock on any
    backend with row-level write atomicity (Postgres); on SQLite's single
    writer it degenerates to a no-op bookkeeping column since there is only
    ever one process able to hold the lease at all."""
    now = _now()
    expires = now + max(1, int(ttl_seconds))
    cur = await db.execute(
        "UPDATE workflow_runs SET lease_owner = ?, lease_expires_at = ? "
        "WHERE id = ? AND (lease_owner IS NULL OR lease_owner = ? OR lease_expires_at IS NULL OR lease_expires_at < ?)",
        (owner, expires, run_id, owner, now),
    )
    await db.commit()
    return (cur.rowcount or 0) > 0


async def release_lease(db: aiosqlite.Connection, run_id: str, owner: str) -> None:
    await db.execute(
        "UPDATE workflow_runs SET lease_owner = NULL, lease_expires_at = NULL "
        "WHERE id = ? AND lease_owner = ?",
        (run_id, owner),
    )
    await db.commit()


# ── step debug (Phase 40 — roadmap fase 8.3) ────────────────────────────────

async def set_run_debug(db: aiosqlite.Connection, run_id: str, debug: dict | None) -> None:
    """Persist (or clear) the run's step-debug state: {breakpoints, pending_node,
    input}. The engine reads it on each debug (re)spawn; the API updates it when
    the user sets breakpoints or an input override."""
    await db.execute(
        "UPDATE workflow_runs SET debug_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(debug) if debug is not None else None, _now(), run_id),
    )
    await db.commit()


async def get_run_debug(db: aiosqlite.Connection, run_id: str) -> dict | None:
    async with db.execute(
        "SELECT debug_json FROM workflow_runs WHERE id = ?", (run_id,)
    ) as cur:
        row = await cur.fetchone()
    try:
        return json.loads(row["debug_json"]) if row and row["debug_json"] else None
    except (KeyError, IndexError, TypeError, ValueError):
        return None


async def list_stale_paused_runs(db: aiosqlite.Connection, older_than: int) -> list[GraphRunOut]:
    """Paused step-debug runs last touched before ``older_than`` (unix ts) — the
    debug-session timeout sweep cancels them so a forgotten run isn't suspended
    forever (roadmap fase 8.3)."""
    async with db.execute(
        "SELECT * FROM workflow_runs WHERE status = 'paused' AND updated_at < ? ORDER BY updated_at ASC",
        (older_than,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_run(r) for r in rows]


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
