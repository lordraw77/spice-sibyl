"""
Phase 29 — graph workflow persistence.

CRUD over ``workflows`` (+ immutable ``workflow_versions``), ``workflow_runs``,
``workflow_node_runs`` and ``workflow_triggers``. The engine
(``workflow_graph_service``) drives run/node-run state; the API layer drives
workflow + trigger CRUD.
"""

import hashlib
import json
import secrets
import time
import uuid

import aiosqlite

from app.schemas.graph_workflows import (
    GraphRunOut,
    GraphWorkflowOut,
    NodeRunOut,
    RunnerOut,
    WorkflowApprovalOut,
    WorkflowGitSyncOut,
    WorkflowGraph,
    WorkflowScheduleOut,
    WorkflowStatsOut,
    WorkflowTriggerOut,
)


def _col(row, name, default=None):
    """Safe column access — tolerates a row from before a column was migrated in."""
    try:
        return row[name]
    except (KeyError, IndexError):
        return default


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

    def _schema(column: str) -> dict | None:
        try:
            value = json.loads(row[column]) if row[column] else None
        except (KeyError, IndexError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    try:
        environments = json.loads(row["environments_json"] or "{}")
    except (KeyError, IndexError, ValueError):
        environments = {}

    def _int_or_none(column: str) -> int | None:
        value = _col(row, column)
        return int(value) if value is not None else None

    def _json_dict(column: str) -> dict:
        try:
            value = json.loads(_col(row, column) or "{}")
        except (ValueError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    try:
        tags = json.loads(_col(row, "tags_json") or "[]")
    except (ValueError, TypeError):
        tags = []

    return GraphWorkflowOut(
        id=row["id"],
        profile_id=row["profile_id"],
        name=row["name"],
        description=row["description"],
        graph=WorkflowGraph.model_validate(json.loads(row["graph_json"])),
        variables=variables if isinstance(variables, dict) else {},
        max_concurrent_runs=max_concurrent_runs,
        input_schema=_schema("input_schema_json"),
        output_schema=_schema("output_schema_json"),
        environments=environments if isinstance(environments, dict) else {},
        expose_as_tool=bool(_col(row, "expose_as_tool")),
        token_budget_month=_int_or_none("token_budget_month"),
        run_budget_month=_int_or_none("run_budget_month"),
        runs_retention_days=_int_or_none("runs_retention_days"),
        blackout=_json_dict("blackout_json"),
        sla=_json_dict("sla_json"),
        notify=_json_dict("notify_json"),
        folder=_col(row, "folder"),
        tags=tags if isinstance(tags, list) else [],
        archived=bool(_col(row, "archived")),
        active=bool(row["active"]),
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        git_sync=_row_to_git_sync(row),
    )


def _row_to_git_sync(row: aiosqlite.Row) -> WorkflowGitSyncOut | None:
    """Fase 13.3 — None when sync was never configured (no repo_url)."""
    repo_url = _col(row, "git_repo_url")
    if not repo_url:
        return None
    return WorkflowGitSyncOut(
        repo_url=repo_url,
        branch=_col(row, "git_branch") or "main",
        token_secret=_col(row, "git_token_secret"),
        subpath=_col(row, "git_subpath"),
        last_synced_at=_col(row, "git_last_synced_at"),
    )


async def create_workflow(
    db: aiosqlite.Connection, profile_id: str, name: str, description: str, graph: WorkflowGraph,
    variables: dict | None = None, max_concurrent_runs: int = 0,
    input_schema: dict | None = None, output_schema: dict | None = None,
    environments: dict | None = None, expose_as_tool: bool = False,
    token_budget_month: int | None = None, run_budget_month: int | None = None,
    runs_retention_days: int | None = None,
) -> GraphWorkflowOut:
    wf_id = str(uuid.uuid4())
    now = _now()
    graph_json = json.dumps(graph.model_dump())
    await db.execute(
        "INSERT INTO workflows (id, profile_id, name, description, graph_json, variables_json, max_concurrent_runs, input_schema_json, output_schema_json, environments_json, expose_as_tool, token_budget_month, run_budget_month, runs_retention_days, active, version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?)",
        (wf_id, profile_id, name, description, graph_json, json.dumps(variables or {}),
         max(0, int(max_concurrent_runs or 0)),
         json.dumps(input_schema) if input_schema else None,
         json.dumps(output_schema) if output_schema else None,
         json.dumps(environments or {}),
         1 if expose_as_tool else 0,
         token_budget_month, run_budget_month, runs_retention_days,
         now, now),
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


async def search_workflows(
    db: aiosqlite.Connection,
    profile_id: str,
    *,
    query: str | None = None,
    folder: str | None = None,
    tag: str | None = None,
    include_archived: bool = False,
) -> list[GraphWorkflowOut]:
    """Fase 17.3 — the workflow navigator. Filters the profile's workflows by
    ``folder`` (exact) and ``tag`` (membership), then applies a full-text ``query``
    over name, description AND the graph node contents (so "slack" finds a workflow
    that merely *uses* a Slack node). Archived workflows are hidden unless
    ``include_archived`` (or a specific folder/tag/query would surface them)."""
    sql = "SELECT * FROM workflows WHERE profile_id = ?"
    args: list = [profile_id]
    if folder is not None:
        sql += " AND IFNULL(folder, '') = ?"
        args.append(folder)
    if not include_archived:
        sql += " AND archived = 0"
    sql += " ORDER BY updated_at DESC"
    async with db.execute(sql, args) as cur:  # noqa: S608 — clauses are fixed literals; values are bound
        rows = await cur.fetchall()

    needle = (query or "").strip().lower()
    tag_l = (tag or "").strip().lower()
    out: list[GraphWorkflowOut] = []
    for r in rows:
        wf = _row_to_workflow(r)
        if tag_l and tag_l not in [t.lower() for t in wf.tags]:
            continue
        if needle:
            haystack = " ".join([
                wf.name or "", wf.description or "",
                (r["graph_json"] or ""),
            ]).lower()
            if needle not in haystack:
                continue
        out.append(wf)
    return out


async def list_folders(db: aiosqlite.Connection, profile_id: str) -> list[str]:
    """Fase 17.3 — distinct non-empty folder names for the navigator tree."""
    async with db.execute(
        "SELECT DISTINCT folder FROM workflows WHERE profile_id = ? AND folder IS NOT NULL AND folder <> '' "
        "ORDER BY folder",
        (profile_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [r["folder"] for r in rows]


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
    input_schema: dict | None = None,
    output_schema: dict | None = None,
    environments: dict | None = None,
    expose_as_tool: bool | None = None,
    token_budget_month: int | None = None, run_budget_month: int | None = None,
    runs_retention_days: int | None = None,
    blackout: dict | None = None, sla: dict | None = None, notify: dict | None = None,
    folder: str | None = None, tags: list | None = None, archived: bool | None = None,
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
        "environments_json = COALESCE(?, environments_json), "
        "expose_as_tool = COALESCE(?, expose_as_tool), "
        "token_budget_month = COALESCE(?, token_budget_month), "
        "run_budget_month = COALESCE(?, run_budget_month), "
        "runs_retention_days = COALESCE(?, runs_retention_days), "
        # Phase 49 (fase 17): blackout/sla/notify/folder/tags/archived — None
        # leaves the column, an empty dict/list clears the JSON to '{}'/'[]'.
        "blackout_json = COALESCE(?, blackout_json), "
        "sla_json = COALESCE(?, sla_json), "
        "notify_json = COALESCE(?, notify_json), "
        "folder = COALESCE(?, folder), "
        "tags_json = COALESCE(?, tags_json), "
        "archived = COALESCE(?, archived), "
        "active = COALESCE(?, active), version = ?, updated_at = ? "
        "WHERE id = ?",
        (
            name,
            description,
            graph_json,
            None if variables is None else json.dumps(variables),
            None if max_concurrent_runs is None else max(0, int(max_concurrent_runs)),
            None if environments is None else json.dumps(environments),
            None if expose_as_tool is None else int(expose_as_tool),
            token_budget_month,
            run_budget_month,
            runs_retention_days,
            None if blackout is None else json.dumps(blackout),
            None if sla is None else json.dumps(sla),
            None if notify is None else json.dumps(notify),
            folder,
            None if tags is None else json.dumps(tags),
            None if archived is None else int(archived),
            None if active is None else int(active),
            version,
            _now(),
            wf_id,
        ),
    )
    # Fase 6.4 contracts — None leaves the column untouched; an EMPTY dict ({})
    # clears the contract (COALESCE can't express "set to NULL").
    for column, value in (("input_schema_json", input_schema), ("output_schema_json", output_schema)):
        if value is None:
            continue
        await db.execute(
            f"UPDATE workflows SET {column} = ? WHERE id = ?",  # noqa: S608 — column from a fixed pair
            (json.dumps(value) if value else None, wf_id),
        )
    await db.commit()
    return await get_workflow(db, wf_id)


async def set_git_sync(
    db: aiosqlite.Connection, wf_id: str, *,
    repo_url: str | None, branch: str, token_secret: str | None, subpath: str | None,
) -> None:
    """Fase 13.3 — configure (or, with an empty ``repo_url``, disable) Git sync
    for this workflow. Only the token's $secrets NAME is stored, never its
    value."""
    await db.execute(
        "UPDATE workflows SET git_repo_url = ?, git_branch = ?, git_token_secret = ?, git_subpath = ? "
        "WHERE id = ?",
        (repo_url or None, branch or "main", token_secret or None, subpath or None, wf_id),
    )
    await db.commit()


async def mark_git_synced(db: aiosqlite.Connection, wf_id: str, when: int) -> None:
    await db.execute("UPDATE workflows SET git_last_synced_at = ? WHERE id = ?", (when, wf_id))
    await db.commit()


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


async def add_draft_version(db: aiosqlite.Connection, wf_id: str, graph: WorkflowGraph) -> int:
    """Fase 13.3 — a pulled Git definition that differs from anything already
    stored becomes a new ``workflow_versions`` row WITHOUT touching the live
    ``workflows.graph_json`` — a pure draft the user reviews/restores like any
    other version (roadmap: "changed definitions become new draft versions")."""
    current = await get_workflow(db, wf_id)
    if current is None:
        raise ValueError("Workflow not found")
    async with db.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM workflow_versions WHERE workflow_id = ?", (wf_id,)
    ) as cur:
        row = await cur.fetchone()
    version = int(row["v"]) + 1
    await _snapshot_version(db, wf_id, version, json.dumps(graph.model_dump()))
    await db.commit()
    return version


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


async def list_callable_workflows(db: aiosqlite.Connection, profile_id: str) -> list[GraphWorkflowOut]:
    """Workflows of the profile that declare an input contract (fase 6.4) —
    exposed in the node catalog as typed ``workflow.<id>`` nodes."""
    async with db.execute(
        "SELECT * FROM workflows WHERE profile_id = ? AND input_schema_json IS NOT NULL "
        "ORDER BY name",
        (profile_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_workflow(r) for r in rows]


async def list_exposed_tool_workflows(db: aiosqlite.Connection, profile_id: str) -> list[GraphWorkflowOut]:
    """Fase 9.1 — the profile's workflows published as callable tools: active,
    with an input contract, and ``expose_as_tool`` set. Ordered by name so the
    tool list is stable."""
    async with db.execute(
        "SELECT * FROM workflows WHERE profile_id = ? AND expose_as_tool = 1 "
        "AND active = 1 AND input_schema_json IS NOT NULL ORDER BY name",
        (profile_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_workflow(r) for r in rows]


# ── chat sessions (Phase 41 — roadmap fase 9.3) ─────────────────────────────

async def get_chat_history(
    db: aiosqlite.Connection, workflow_id: str, session_id: str
) -> list[dict]:
    """The rolling conversation turns for a (workflow, session) — [] when new."""
    async with db.execute(
        "SELECT history_json FROM workflow_chat_sessions WHERE workflow_id = ? AND session_id = ?",
        (workflow_id, session_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return []
    try:
        hist = json.loads(row["history_json"] or "[]")
    except (ValueError, KeyError):
        return []
    return hist if isinstance(hist, list) else []


async def upsert_chat_history(
    db: aiosqlite.Connection, workflow_id: str, profile_id: str,
    session_id: str, history: list[dict],
) -> None:
    now = _now()
    await db.execute(
        "INSERT INTO workflow_chat_sessions (id, session_id, workflow_id, profile_id, history_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(workflow_id, session_id) DO UPDATE SET history_json = excluded.history_json, updated_at = excluded.updated_at",
        (str(uuid.uuid4()), session_id, workflow_id, profile_id, json.dumps(history), now, now),
    )
    await db.commit()


async def purge_stale_chat_sessions(db: aiosqlite.Connection, older_than: int) -> int:
    """Delete chat sessions idle since before ``older_than`` (unix ts). Returns
    the number removed."""
    cur = await db.execute(
        "DELETE FROM workflow_chat_sessions WHERE updated_at < ?", (older_than,)
    )
    await db.commit()
    return cur.rowcount or 0


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


# ── test cases (Phase 43 — roadmap fase 11.1) ───────────────────────────────

def _row_to_test_case(row: aiosqlite.Row) -> "WorkflowTestCaseOut":
    from app.schemas.graph_workflows import WorkflowTestCaseOut

    return WorkflowTestCaseOut(
        id=row["id"],
        workflow_id=row["workflow_id"],
        name=row["name"],
        trigger_payload=json.loads(row["trigger_payload_json"] or "{}"),
        assertions=json.loads(row["assertions_json"] or "[]"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def create_test_case(
    db: aiosqlite.Connection, workflow_id: str, name: str,
    trigger_payload: dict, assertions: list[dict],
) -> str:
    case_id = str(uuid.uuid4())
    now = _now()
    await db.execute(
        "INSERT INTO workflow_test_cases "
        "(id, workflow_id, name, trigger_payload_json, assertions_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (case_id, workflow_id, name, json.dumps(trigger_payload), json.dumps(assertions), now, now),
    )
    await db.commit()
    return case_id


async def list_test_cases(db: aiosqlite.Connection, workflow_id: str) -> list["WorkflowTestCaseOut"]:
    async with db.execute(
        "SELECT * FROM workflow_test_cases WHERE workflow_id = ? ORDER BY created_at", (workflow_id,)
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_test_case(r) for r in rows]


async def get_test_case(db: aiosqlite.Connection, case_id: str) -> "WorkflowTestCaseOut | None":
    async with db.execute("SELECT * FROM workflow_test_cases WHERE id = ?", (case_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_test_case(row) if row else None


async def update_test_case(
    db: aiosqlite.Connection, case_id: str, name: str,
    trigger_payload: dict, assertions: list[dict],
) -> bool:
    cur = await db.execute(
        "UPDATE workflow_test_cases SET name = ?, trigger_payload_json = ?, assertions_json = ?, updated_at = ? "
        "WHERE id = ?",
        (name, json.dumps(trigger_payload), json.dumps(assertions), _now(), case_id),
    )
    await db.commit()
    return cur.rowcount > 0


async def delete_test_case(db: aiosqlite.Connection, case_id: str) -> bool:
    cur = await db.execute("DELETE FROM workflow_test_cases WHERE id = ?", (case_id,))
    await db.commit()
    return cur.rowcount > 0


# ── stats (Phase 36 — roadmap fase 5.1; environment filter — Phase 39 fase 7.2) ─

async def workflow_stats_for_profile(
    db: aiosqlite.Connection, profile_id: str, *, environment: str | None = None,
) -> list[WorkflowStatsOut]:
    """Per-workflow aggregates: run counts by outcome, success rate over terminal
    runs, average terminal-run duration, and the LLM token totals summed from the
    `_usage` key that llm.* node outputs carry (json_extract over output_json).

    ``environment`` (Phase 39 — roadmap fase 7.2, extending fase 5.1) optionally
    scopes every aggregate to runs executed in that named environment — e.g.
    comparing `prod` health against the unfiltered (all-environments) totals.
    The join condition (not a WHERE filter) keeps every workflow in the result
    even when it has zero runs in the requested environment, matching the
    unfiltered endpoint's shape (0 runs, not an omitted row)."""
    env_clause = "" if environment is None else "AND r.environment = ?"
    env_params: list = [] if environment is None else [environment]

    async with db.execute(
        f"""
        SELECT w.id, w.name, w.active,
               COUNT(r.id)                                            AS runs,
               SUM(CASE WHEN r.status = 'completed' THEN 1 ELSE 0 END) AS completed,
               SUM(CASE WHEN r.status = 'failed'    THEN 1 ELSE 0 END) AS failed,
               SUM(CASE WHEN r.status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
               AVG(CASE WHEN r.status IN ('completed', 'failed')
                        THEN r.updated_at - r.created_at END)          AS avg_duration_s,
               MAX(r.created_at)                                       AS last_run_at
        FROM workflows w
        LEFT JOIN workflow_runs r ON r.workflow_id = w.id {env_clause}
        WHERE w.profile_id = ?
        GROUP BY w.id
        ORDER BY w.updated_at DESC
        """,  # noqa: S608 — env_clause is a fixed literal, never interpolated user input
        (*env_params, profile_id),
    ) as cur:
        rows = await cur.fetchall()

    async with db.execute(
        f"""
        SELECT r.workflow_id,
               SUM(COALESCE(json_extract(nr.output_json, '$._usage.tokens_in'), 0))    AS tokens_in,
               SUM(COALESCE(json_extract(nr.output_json, '$._usage.tokens_out'), 0))   AS tokens_out,
               SUM(COALESCE(json_extract(nr.output_json, '$._usage.tokens_total'), 0)) AS tokens_total
        FROM workflow_node_runs nr
        JOIN workflow_runs r ON r.id = nr.run_id
        JOIN workflows w ON w.id = r.workflow_id
        WHERE w.profile_id = ? AND nr.output_json LIKE '%_usage%' {env_clause}
        GROUP BY r.workflow_id
        """,  # noqa: S608 — env_clause is a fixed literal, never interpolated user input
        (profile_id, *env_params),
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


# ── per-node metrics (Phase 39 — roadmap fase 7.4) ──────────────────────────

def _percentile(sorted_values: list[float], q: float) -> float | None:
    """Nearest-rank percentile over an ascending list (None when empty)."""
    if not sorted_values:
        return None
    idx = min(len(sorted_values) - 1, max(0, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[idx]


async def node_stats_for_workflow(db: aiosqlite.Connection, workflow_id: str):
    """Per-node aggregates over the workflow's run history: executions by
    outcome, error rate, avg/p50/p95 duration and LLM tokens (from the
    ``_usage`` key of the node outputs). Percentiles are computed in Python —
    SQLite has no percentile aggregate — over at most the most recent rows the
    query returns, which is fine at the per-workflow scale this serves."""
    from app.schemas.graph_workflows import WorkflowNodeStatsOut

    async with db.execute(
        """
        SELECT nr.node_id, nr.node_type, nr.status, nr.started_at, nr.finished_at,
               COALESCE(json_extract(nr.output_json, '$._usage.tokens_total'), 0) AS tokens_total
        FROM workflow_node_runs nr
        JOIN workflow_runs r ON r.id = nr.run_id
        WHERE r.workflow_id = ?
        ORDER BY nr.started_at ASC
        """,
        (workflow_id,),
    ) as cur:
        rows = await cur.fetchall()

    acc: dict[str, dict] = {}
    for r in rows:
        entry = acc.setdefault(r["node_id"], {
            "node_type": r["node_type"], "executions": 0, "ok": 0, "error": 0,
            "skipped": 0, "durations": [], "tokens_total": 0, "last_executed_at": None,
        })
        entry["node_type"] = r["node_type"]
        entry["executions"] += 1
        if r["status"] in ("ok", "error", "skipped"):
            entry[r["status"]] += 1
        if r["status"] in ("ok", "error") and r["started_at"] and r["finished_at"]:
            entry["durations"].append(float(r["finished_at"] - r["started_at"]))
        try:
            entry["tokens_total"] += int(r["tokens_total"] or 0)
        except (TypeError, ValueError):
            pass
        if r["started_at"]:
            entry["last_executed_at"] = max(entry["last_executed_at"] or 0, r["started_at"])

    out = []
    for node_id, e in acc.items():
        durations = sorted(e["durations"])
        terminal = e["ok"] + e["error"]
        out.append(WorkflowNodeStatsOut(
            node_id=node_id,
            node_type=e["node_type"],
            executions=e["executions"],
            ok=e["ok"],
            error=e["error"],
            skipped=e["skipped"],
            error_rate=(e["error"] / terminal) if terminal else None,
            avg_duration_s=(sum(durations) / len(durations)) if durations else None,
            p50_duration_s=_percentile(durations, 0.50),
            p95_duration_s=_percentile(durations, 0.95),
            tokens_total=e["tokens_total"],
            last_executed_at=e["last_executed_at"],
        ))
    out.sort(key=lambda s: (-(s.error_rate or 0), -(s.p95_duration_s or 0)))
    return out


# ── prompt A/B variant metrics (Phase 50 — roadmap fase 18.2) ───────────────

async def variant_stats_for_node(db: aiosqlite.Connection, workflow_id: str, node_id: str):
    """Per-variant aggregates for one node over the workflow's run history: how
    each ``_variant`` performed (executions, ok/error, judge score, tokens). Runs
    without a recorded variant fall under ``(default)``. The leading variant
    (highest avg judge score, else highest ok-rate) is flagged ``winner`` so the
    editor can offer "promote variant". Reads the same node-run outputs as the
    fase 7.4 metrics — no extra bookkeeping."""
    from app.schemas.graph_workflows import WorkflowNodeVariantStatsOut

    async with db.execute(
        """
        SELECT nr.status,
               COALESCE(json_extract(nr.output_json, '$._variant'), '(default)') AS variant,
               json_extract(nr.output_json, '$.score')                AS score,
               json_extract(nr.output_json, '$.passed')               AS passed,
               COALESCE(json_extract(nr.output_json, '$._usage.tokens_total'), 0) AS tokens_total
        FROM workflow_node_runs nr
        JOIN workflow_runs r ON r.id = nr.run_id
        WHERE r.workflow_id = ? AND nr.node_id = ? AND nr.status IN ('ok', 'error')
        """,
        (workflow_id, node_id),
    ) as cur:
        rows = await cur.fetchall()

    acc: dict[str, dict] = {}
    for r in rows:
        e = acc.setdefault(r["variant"], {
            "executions": 0, "ok": 0, "error": 0, "scores": [], "passes": 0, "judged": 0, "tokens": [],
        })
        e["executions"] += 1
        if r["status"] in ("ok", "error"):
            e[r["status"]] += 1
        if isinstance(r["score"], (int, float)):
            e["scores"].append(float(r["score"]))
        if r["passed"] is not None:
            e["judged"] += 1
            if r["passed"]:
                e["passes"] += 1
        try:
            e["tokens"].append(int(r["tokens_total"] or 0))
        except (TypeError, ValueError):
            pass

    out = []
    for variant, e in acc.items():
        terminal = e["ok"] + e["error"]
        out.append(WorkflowNodeVariantStatsOut(
            variant=variant,
            executions=e["executions"],
            ok=e["ok"],
            error=e["error"],
            ok_rate=(e["ok"] / terminal) if terminal else None,
            avg_score=(sum(e["scores"]) / len(e["scores"])) if e["scores"] else None,
            pass_rate=(e["passes"] / e["judged"]) if e["judged"] else None,
            avg_tokens=(sum(e["tokens"]) / len(e["tokens"])) if e["tokens"] else None,
        ))
    # Winner: best avg judge score, else best ok-rate; needs ≥2 variants to matter.
    ranked = [s for s in out if s.variant != "(default)"] or out
    if ranked:
        best = max(ranked, key=lambda s: (
            s.avg_score if s.avg_score is not None else -1.0,
            s.ok_rate if s.ok_rate is not None else -1.0,
            s.executions,
        ))
        best.winner = True
    out.sort(key=lambda s: (-(s.avg_score or -1.0), -(s.ok_rate or -1.0)))
    return out


# ── budgets and quotas (Phase 44 — roadmap fase 12.1) ───────────────────────

async def workflow_usage_for_period(db: aiosqlite.Connection, workflow_id: str, period_start: int) -> dict:
    """Runs and total LLM tokens for one workflow since ``period_start`` (a
    calendar-month epoch) — the same sources as the fase 5.1 stats, just
    time-boxed instead of lifetime, so budgets can be checked cheaply without a
    duplicated usage counter (reset "for free" as the period rolls over)."""
    async with db.execute(
        "SELECT COUNT(*) AS runs FROM workflow_runs WHERE workflow_id = ? AND created_at >= ?",
        (workflow_id, period_start),
    ) as cur:
        runs = (await cur.fetchone())["runs"] or 0
    async with db.execute(
        """
        SELECT SUM(COALESCE(json_extract(nr.output_json, '$._usage.tokens_total'), 0)) AS tokens_total
        FROM workflow_node_runs nr
        JOIN workflow_runs r ON r.id = nr.run_id
        WHERE r.workflow_id = ? AND r.created_at >= ? AND nr.output_json LIKE '%_usage%'
        """,
        (workflow_id, period_start),
    ) as cur:
        tokens_total = (await cur.fetchone())["tokens_total"] or 0
    return {"runs": int(runs), "tokens_total": int(tokens_total)}


async def profile_usage_for_period(db: aiosqlite.Connection, profile_id: str, period_start: int) -> dict:
    """Same as :func:`workflow_usage_for_period`, summed over every workflow
    owned by ``profile_id`` — the profile-wide ("workspace") usage."""
    async with db.execute(
        "SELECT COUNT(*) AS runs FROM workflow_runs WHERE profile_id = ? AND created_at >= ?",
        (profile_id, period_start),
    ) as cur:
        runs = (await cur.fetchone())["runs"] or 0
    async with db.execute(
        """
        SELECT SUM(COALESCE(json_extract(nr.output_json, '$._usage.tokens_total'), 0)) AS tokens_total
        FROM workflow_node_runs nr
        JOIN workflow_runs r ON r.id = nr.run_id
        WHERE r.profile_id = ? AND r.created_at >= ? AND nr.output_json LIKE '%_usage%'
        """,
        (profile_id, period_start),
    ) as cur:
        tokens_total = (await cur.fetchone())["tokens_total"] or 0
    return {"runs": int(runs), "tokens_total": int(tokens_total)}


async def set_workflow_budget_warned(db: aiosqlite.Connection, workflow_id: str, period: str) -> None:
    await db.execute(
        "UPDATE workflows SET budget_warned_period = ? WHERE id = ?", (period, workflow_id)
    )
    await db.commit()


async def get_profile_budget(db: aiosqlite.Connection, profile_id: str) -> dict | None:
    async with db.execute(
        "SELECT * FROM profile_budgets WHERE profile_id = ?", (profile_id,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return {
        "profile_id": row["profile_id"],
        "token_budget_month": row["token_budget_month"],
        "run_budget_month": row["run_budget_month"],
        "warned_period": row["warned_period"],
    }


async def set_profile_budget(
    db: aiosqlite.Connection, profile_id: str,
    token_budget_month: int | None, run_budget_month: int | None,
) -> None:
    await db.execute(
        "INSERT INTO profile_budgets (profile_id, token_budget_month, run_budget_month, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(profile_id) DO UPDATE SET "
        "token_budget_month = excluded.token_budget_month, "
        "run_budget_month = excluded.run_budget_month, "
        "updated_at = excluded.updated_at",
        (profile_id, token_budget_month, run_budget_month, _now()),
    )
    await db.commit()


async def set_profile_budget_warned(db: aiosqlite.Connection, profile_id: str, period: str) -> None:
    await db.execute(
        "UPDATE profile_budgets SET warned_period = ? WHERE profile_id = ?", (period, profile_id)
    )
    await db.commit()


# ── run retention (Phase 44 — roadmap fase 12.2) ────────────────────────────

async def purge_old_runs(db: aiosqlite.Connection, default_days: int, now: int) -> int:
    """Delete terminal runs (completed/failed/cancelled — never queued/pending/
    running/waiting/paused) older than the workflow's own ``runs_retention_days``
    override, falling back to ``default_days`` (0 disables purging for a
    workflow). ``workflow_node_runs`` cascade via the FK. Returns rows deleted."""
    if default_days <= 0:
        clause_default_disabled = "AND w.runs_retention_days IS NOT NULL AND w.runs_retention_days > 0"
    else:
        clause_default_disabled = ""
    cur = await db.execute(
        f"""
        DELETE FROM workflow_runs
        WHERE id IN (
            SELECT r.id FROM workflow_runs r
            JOIN workflows w ON w.id = r.workflow_id
            WHERE r.status IN ('completed', 'failed', 'cancelled')
              {clause_default_disabled}
              AND COALESCE(w.runs_retention_days, ?) > 0
              AND r.created_at < (? - COALESCE(w.runs_retention_days, ?) * 86400)
        )
        """,  # noqa: S608 — clause_default_disabled is a fixed literal, never interpolated user input
        (default_days, now, default_days),
    )
    await db.commit()
    return cur.rowcount or 0


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


# ── remote runners (Phase 46 — roadmap fase 14.1) ───────────────────────────

def _hash_runner_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _row_to_runner(row: aiosqlite.Row, *, heartbeat_timeout: int) -> RunnerOut:
    last_hb = _col(row, "last_heartbeat_at")
    online = bool(last_hb) and (_now() - last_hb) <= heartbeat_timeout
    try:
        labels = json.loads(row["labels_json"] or "[]")
    except (ValueError, TypeError):
        labels = []
    try:
        allowed = json.loads(row["allowed_node_types_json"] or "[]")
    except (ValueError, TypeError):
        allowed = []
    return RunnerOut(
        id=row["id"],
        name=row["name"],
        labels=labels if isinstance(labels, list) else [],
        allowed_node_types=allowed if isinstance(allowed, list) else [],
        version=_col(row, "version"),
        status="online" if online else "offline",
        last_heartbeat_at=last_hb,
        created_at=row["created_at"],
    )


async def create_runner(
    db: aiosqlite.Connection, profile_id: str, name: str,
    labels: list[str], allowed_node_types: list[str],
) -> tuple[str, str]:
    """Provision a runner slot; returns (id, raw_token) — the raw token is
    NEVER stored (only its sha256) and never retrievable again."""
    runner_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    await db.execute(
        "INSERT INTO workflow_runners (id, profile_id, name, token_hash, labels_json, allowed_node_types_json, revoked, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
        (runner_id, profile_id, name, _hash_runner_token(token),
         json.dumps(labels), json.dumps(allowed_node_types), _now()),
    )
    await db.commit()
    return runner_id, token


async def get_runner_row(db: aiosqlite.Connection, runner_id: str) -> aiosqlite.Row | None:
    async with db.execute(
        "SELECT * FROM workflow_runners WHERE id = ? AND revoked = 0", (runner_id,)
    ) as cur:
        return await cur.fetchone()


async def get_runner_by_token(db: aiosqlite.Connection, token: str) -> aiosqlite.Row | None:
    async with db.execute(
        "SELECT * FROM workflow_runners WHERE token_hash = ? AND revoked = 0",
        (_hash_runner_token(token),),
    ) as cur:
        return await cur.fetchone()


async def list_runners(db: aiosqlite.Connection, profile_id: str, *, heartbeat_timeout: int) -> list[RunnerOut]:
    async with db.execute(
        "SELECT * FROM workflow_runners WHERE profile_id = ? AND revoked = 0 ORDER BY created_at DESC",
        (profile_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_runner(r, heartbeat_timeout=heartbeat_timeout) for r in rows]


async def find_online_runners(
    db: aiosqlite.Connection, profile_id: str, label: str, node_type: str, *, heartbeat_timeout: int,
) -> list[dict]:
    """Runners of the profile that: are online (heartbeat within the timeout),
    carry ``label``, and (empty allow-list, or) allow ``node_type``. Ordered
    oldest-heartbeat-first so load spreads round-robin-ish across runners."""
    cutoff = _now() - heartbeat_timeout
    async with db.execute(
        "SELECT * FROM workflow_runners WHERE profile_id = ? AND revoked = 0 "
        "AND last_heartbeat_at IS NOT NULL AND last_heartbeat_at >= ? "
        "ORDER BY last_heartbeat_at ASC",
        (profile_id, cutoff),
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        try:
            labels = json.loads(r["labels_json"] or "[]")
        except (ValueError, TypeError):
            labels = []
        if label not in labels:
            continue
        try:
            allowed = json.loads(r["allowed_node_types_json"] or "[]")
        except (ValueError, TypeError):
            allowed = []
        if allowed and node_type not in allowed:
            continue
        out.append(dict(r))
    return out


async def heartbeat_runner(
    db: aiosqlite.Connection, runner_id: str, *, version: str | None, labels: list[str] | None,
) -> None:
    if labels is not None:
        await db.execute(
            "UPDATE workflow_runners SET last_heartbeat_at = ?, version = COALESCE(?, version), labels_json = ? WHERE id = ?",
            (_now(), version, json.dumps(labels), runner_id),
        )
    else:
        await db.execute(
            "UPDATE workflow_runners SET last_heartbeat_at = ?, version = COALESCE(?, version) WHERE id = ?",
            (_now(), version, runner_id),
        )
    await db.commit()


async def revoke_runner(db: aiosqlite.Connection, runner_id: str) -> None:
    await db.execute("UPDATE workflow_runners SET revoked = 1 WHERE id = ?", (runner_id,))
    await db.commit()


# ── remote runner jobs (Phase 46 — roadmap fase 14.1) ───────────────────────

async def create_runner_job(
    db: aiosqlite.Connection, runner_id: str, run_id: str | None,
    node_id: str, node_type: str, payload: dict,
) -> str:
    job_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO workflow_runner_jobs (id, runner_id, run_id, node_id, node_type, payload_json, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)",
        (job_id, runner_id, run_id, node_id, node_type, json.dumps(payload), _now()),
    )
    await db.commit()
    return job_id


async def get_runner_job(db: aiosqlite.Connection, job_id: str) -> aiosqlite.Row | None:
    async with db.execute("SELECT * FROM workflow_runner_jobs WHERE id = ?", (job_id,)) as cur:
        return await cur.fetchone()


async def claim_next_runner_job(db: aiosqlite.Connection, runner_id: str) -> aiosqlite.Row | None:
    """Atomically claim the oldest queued job assigned to this runner (a
    conditional UPDATE, so two concurrent polls from a hiccuping runner client
    never both claim the same job)."""
    async with db.execute(
        "SELECT id FROM workflow_runner_jobs WHERE runner_id = ? AND status = 'queued' "
        "ORDER BY created_at ASC LIMIT 1",
        (runner_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    cur = await db.execute(
        "UPDATE workflow_runner_jobs SET status = 'claimed', claimed_at = ? WHERE id = ? AND status = 'queued'",
        (_now(), row["id"]),
    )
    await db.commit()
    if not cur.rowcount:
        return None
    return await get_runner_job(db, row["id"])


async def finish_runner_job(
    db: aiosqlite.Connection, job_id: str, *, ok: bool, result: dict | None = None, error: str | None = None,
) -> bool:
    cur = await db.execute(
        "UPDATE workflow_runner_jobs SET status = ?, result_json = ?, error = ?, finished_at = ? "
        "WHERE id = ? AND status IN ('queued', 'claimed')",
        ("done" if ok else "failed", json.dumps(result) if result is not None else None, error, _now(), job_id),
    )
    await db.commit()
    return (cur.rowcount or 0) > 0


async def timeout_runner_job(db: aiosqlite.Connection, job_id: str) -> None:
    await db.execute(
        "UPDATE workflow_runner_jobs SET status = 'timeout', finished_at = ? "
        "WHERE id = ? AND status IN ('queued', 'claimed')",
        (_now(), job_id),
    )
    await db.commit()


# ── message queue (Phase 46 — roadmap fase 14.4) ────────────────────────────
# Backing store for the `db` QueueDriver — see workflow_graph_service.QueueDriver.

async def publish_queue_message(db: aiosqlite.Connection, topic: str, payload: dict, headers: dict) -> str:
    msg_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO workflow_queue_messages (id, topic, payload_json, headers_json, status, created_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?)",
        (msg_id, topic, json.dumps(payload), json.dumps(headers or {}), _now()),
    )
    await db.commit()
    return msg_id


async def consume_queue_messages(db: aiosqlite.Connection, topic: str, limit: int = 10) -> list[dict]:
    """Claim (mark consumed) up to ``limit`` pending messages of ``topic``,
    oldest first. The caller (``_poll_queue_consume``) fires the triggered
    workflow run immediately after claiming each message, so the delivery
    window between the two is as small as a single ``run_workflow`` call —
    a crash inside that narrow window is the only way to lose a message; a
    broker-backed ``QueueDriver`` (a future real adapter) can offer a stronger
    ack-after-completion guarantee by delaying the status flip until then."""
    async with db.execute(
        "SELECT * FROM workflow_queue_messages WHERE topic = ? AND status = 'pending' "
        "ORDER BY created_at ASC LIMIT ?",
        (topic, limit),
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        cur2 = await db.execute(
            "UPDATE workflow_queue_messages SET status = 'consumed', consumed_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (_now(), r["id"]),
        )
        if cur2.rowcount:
            try:
                payload = json.loads(r["payload_json"])
            except (ValueError, TypeError):
                payload = None
            try:
                headers = json.loads(r["headers_json"] or "{}")
            except (ValueError, TypeError):
                headers = {}
            out.append({"id": r["id"], "topic": topic, "message": payload, "headers": headers})
    await db.commit()
    return out


# ── persistent state (Phase 48 — roadmap fase 16.1) ─────────────────────────
# A per-workflow key/value store that survives across runs. Values are JSON;
# `expires_at` is an optional absolute-epoch TTL. Reads apply lazy expiry (an
# expired key reads as absent); `purge_expired_state` reclaims the rows.

async def state_get(db: aiosqlite.Connection, workflow_id: str, key: str, now: int | None = None) -> tuple[bool, object]:
    """Return ``(found, value)`` for ``key``. A missing or expired key yields
    ``(False, None)``. Never raises on a corrupt row — it reads as absent."""
    now = _now() if now is None else now
    async with db.execute(
        "SELECT value_json, expires_at FROM workflow_state WHERE workflow_id = ? AND key = ?",
        (workflow_id, key),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return False, None
    if row["expires_at"] is not None and row["expires_at"] <= now:
        return False, None
    try:
        return True, json.loads(row["value_json"])
    except (ValueError, TypeError):
        return False, None


async def state_set(
    db: aiosqlite.Connection, workflow_id: str, key: str, value: object, ttl_seconds: int | None = None,
) -> int | None:
    """Upsert ``key`` to ``value``. ``ttl_seconds`` > 0 sets an absolute expiry
    (returned); None/0 clears any expiry (the key persists indefinitely)."""
    now = _now()
    expires = now + int(ttl_seconds) if ttl_seconds and ttl_seconds > 0 else None
    await db.execute(
        "INSERT INTO workflow_state (workflow_id, key, value_json, expires_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(workflow_id, key) DO UPDATE SET "
        "value_json = excluded.value_json, expires_at = excluded.expires_at, updated_at = excluded.updated_at",
        (workflow_id, key, json.dumps(value), expires, now),
    )
    await db.commit()
    return expires


async def state_increment(
    db: aiosqlite.Connection, workflow_id: str, key: str, amount: float = 1, ttl_seconds: int | None = None,
) -> float:
    """Atomically add ``amount`` to the numeric value at ``key`` (0 when missing,
    expired or non-numeric) and return the new value. SQLite's single writer
    makes the read-modify-write atomic against other engine callers."""
    found, current = await state_get(db, workflow_id, key)
    base = current if found and isinstance(current, (int, float)) and not isinstance(current, bool) else 0
    new_value = base + amount
    # Keep integers integral so a counter reads back as 3, not 3.0.
    if isinstance(new_value, float) and new_value.is_integer():
        new_value = int(new_value)
    await state_set(db, workflow_id, key, new_value, ttl_seconds)
    return new_value


async def state_list(db: aiosqlite.Connection, workflow_id: str, now: int | None = None) -> list[dict]:
    """Every live (non-expired) key of the workflow, newest first — for the run
    panel's state inspector (fase 16.1)."""
    now = _now() if now is None else now
    async with db.execute(
        "SELECT key, value_json, expires_at, updated_at FROM workflow_state "
        "WHERE workflow_id = ? AND (expires_at IS NULL OR expires_at > ?) "
        "ORDER BY updated_at DESC",
        (workflow_id, now),
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        try:
            value = json.loads(r["value_json"])
        except (ValueError, TypeError):
            value = None
        out.append({"key": r["key"], "value": value, "expires_at": r["expires_at"], "updated_at": r["updated_at"]})
    return out


async def state_delete(db: aiosqlite.Connection, workflow_id: str, key: str) -> bool:
    cur = await db.execute(
        "DELETE FROM workflow_state WHERE workflow_id = ? AND key = ?", (workflow_id, key)
    )
    await db.commit()
    return (cur.rowcount or 0) > 0


async def purge_expired_state(db: aiosqlite.Connection, now: int | None = None) -> int:
    now = _now() if now is None else now
    cur = await db.execute(
        "DELETE FROM workflow_state WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,)
    )
    await db.commit()
    return cur.rowcount or 0


# ── trigger idempotency (Phase 48 — roadmap fase 16.2) ──────────────────────

async def dedup_lookup(db: aiosqlite.Connection, trigger_id: str, key: str, now: int) -> str | None:
    """The ``run_id`` recorded for ``(trigger_id, key)`` if it is still within its
    dedup window, else None (a first — or expired — delivery)."""
    async with db.execute(
        "SELECT run_id FROM workflow_trigger_dedup WHERE trigger_id = ? AND dedup_key = ? AND expires_at > ?",
        (trigger_id, key, now),
    ) as cur:
        row = await cur.fetchone()
    return row["run_id"] if row else None


async def dedup_record(db: aiosqlite.Connection, trigger_id: str, key: str, run_id: str, expires_at: int) -> None:
    """Record (or refresh, when a previous key had expired) the run started for
    ``(trigger_id, key)`` and its new expiry."""
    now = _now()
    await db.execute(
        "INSERT INTO workflow_trigger_dedup (trigger_id, dedup_key, run_id, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(trigger_id, dedup_key) DO UPDATE SET "
        "run_id = excluded.run_id, expires_at = excluded.expires_at, created_at = excluded.created_at",
        (trigger_id, key, run_id, expires_at, now),
    )
    await db.commit()


async def purge_expired_dedup(db: aiosqlite.Connection, now: int | None = None) -> int:
    now = _now() if now is None else now
    cur = await db.execute(
        "DELETE FROM workflow_trigger_dedup WHERE expires_at <= ?", (now,)
    )
    await db.commit()
    return cur.rowcount or 0


# ── Phase 49 (roadmap fase 17.2) — SLA monitors ─────────────────────────────

async def list_runs_over_duration(
    db: aiosqlite.Connection, now_ts: int
) -> list[dict]:
    """Runs that breached their workflow's ``sla.max_duration_s`` and have not yet
    raised the one-time alert (``sla_alerted = 0``). A *running* run counts once
    ``now - created_at`` exceeds the threshold; a terminal run once its total
    elapsed (``updated_at - created_at``) did. Joined to the workflow's SLA config
    and name so the sweep can alert and mark without a second query."""
    async with db.execute(
        "SELECT r.id, r.workflow_id, r.profile_id, r.status, r.created_at, r.updated_at, "
        "w.name AS workflow_name, w.sla_json "
        "FROM workflow_runs r JOIN workflows w ON w.id = r.workflow_id "
        "WHERE r.sla_alerted = 0 AND w.sla_json IS NOT NULL AND w.sla_json <> '{}' "
        "AND r.status IN ('running','pending','completed','failed')",
    ) as cur:
        rows = await cur.fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            sla = json.loads(r["sla_json"] or "{}")
        except (ValueError, TypeError):
            continue
        max_s = int(sla.get("max_duration_s") or 0)
        if max_s <= 0:
            continue
        if r["status"] in ("running", "pending"):
            elapsed = now_ts - r["created_at"]
        else:
            elapsed = (r["updated_at"] or r["created_at"]) - r["created_at"]
        if elapsed > max_s:
            d = dict(r)
            d["sla"] = sla
            d["elapsed_s"] = elapsed
            out.append(d)
    return out


async def mark_run_sla_alerted(db: aiosqlite.Connection, run_id: str) -> None:
    await db.execute("UPDATE workflow_runs SET sla_alerted = 1 WHERE id = ?", (run_id,))
    await db.commit()


async def list_overdue_schedule_triggers(db: aiosqlite.Connection, now_ts: int) -> list[dict]:
    """Fase 17.2 (missed beat) — enabled schedule triggers whose ``next_run_at``
    is set but so far in the past that the run never started (workflow inactive,
    scheduler was down, firing wedged). The per-workflow ``sla.missed_grace_s``
    (or 0 = disabled) sets how overdue is "missed"; ``last_sla_alert_at`` dedups
    so one miss raises one alert. Joined to workflow name/sla/active."""
    async with db.execute(
        "SELECT t.id, t.workflow_id, t.next_run_at, t.last_sla_alert_at, "
        "w.profile_id AS wf_profile_id, w.name AS workflow_name, w.sla_json, w.active AS wf_active "
        "FROM workflow_triggers t JOIN workflows w ON w.id = t.workflow_id "
        "WHERE t.type = 'schedule' AND t.enabled = 1 AND t.next_run_at IS NOT NULL",
    ) as cur:
        rows = await cur.fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            sla = json.loads(r["sla_json"] or "{}")
        except (ValueError, TypeError):
            continue
        grace = int(sla.get("missed_grace_s") or 0)
        if grace <= 0:
            continue
        if now_ts <= r["next_run_at"] + grace:
            continue
        # Dedup: only alert once per missed beat (last alert older than the beat).
        if r["last_sla_alert_at"] and r["last_sla_alert_at"] >= r["next_run_at"]:
            continue
        d = dict(r)
        d["sla"] = sla
        d["overdue_s"] = now_ts - r["next_run_at"]
        out.append(d)
    return out


async def mark_trigger_sla_alerted(db: aiosqlite.Connection, trigger_id: str, when: int) -> None:
    await db.execute(
        "UPDATE workflow_triggers SET last_sla_alert_at = ? WHERE id = ?", (when, trigger_id)
    )
    await db.commit()


# ── Phase 49 (roadmap fase 17.5) — notification digest buffer ───────────────

async def enqueue_digest(
    db: aiosqlite.Connection, workflow_id: str, profile_id: str, channel: str,
    outcome: str, run_id: str,
) -> None:
    await db.execute(
        "INSERT INTO workflow_notification_digest (id, workflow_id, profile_id, channel, outcome, run_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), workflow_id, profile_id, channel, outcome, run_id, _now()),
    )
    await db.commit()


async def list_digest_groups(db: aiosqlite.Connection) -> list[dict]:
    """Distinct (workflow, channel) buckets with a pending digest, with the count
    per outcome and the oldest/newest entry timestamps — the unit the flush sweep
    reasons about ("is this bucket old enough to send?")."""
    async with db.execute(
        "SELECT d.workflow_id, d.profile_id, d.channel, w.name AS workflow_name, w.notify_json, "
        "COUNT(*) AS total, MIN(d.created_at) AS oldest, MAX(d.created_at) AS newest "
        "FROM workflow_notification_digest d JOIN workflows w ON w.id = d.workflow_id "
        "GROUP BY d.workflow_id, d.channel",
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def digest_outcome_counts(
    db: aiosqlite.Connection, workflow_id: str, channel: str
) -> dict[str, int]:
    async with db.execute(
        "SELECT outcome, COUNT(*) AS n FROM workflow_notification_digest "
        "WHERE workflow_id = ? AND channel = ? GROUP BY outcome",
        (workflow_id, channel),
    ) as cur:
        rows = await cur.fetchall()
    return {r["outcome"]: r["n"] for r in rows}


async def clear_digest(db: aiosqlite.Connection, workflow_id: str, channel: str) -> int:
    cur = await db.execute(
        "DELETE FROM workflow_notification_digest WHERE workflow_id = ? AND channel = ?",
        (workflow_id, channel),
    )
    await db.commit()
    return cur.rowcount or 0


# ── custom nodes (Phase 51 / roadmap fase 19) ────────────────────────────────

def _custom_node_row(row) -> dict:
    """Serialise a ``custom_nodes`` row (manifest re-parsed from JSON)."""
    try:
        manifest = json.loads(row["manifest_json"])
    except (ValueError, TypeError):
        manifest = {}
    return {
        "id": row["id"],
        "profile_id": row["profile_id"],
        "type": row["type"],
        "version": row["version"],
        "name": row["name"],
        "description": row["description"],
        "category": row["category"],
        "icon": row["icon"],
        "kind": row["kind"],
        "manifest": manifest,
        "code": _col(row, "code"),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def custom_node_next_version(db: aiosqlite.Connection, profile_id: str, node_type: str) -> int:
    async with db.execute(
        "SELECT MAX(version) AS v FROM custom_nodes WHERE profile_id = ? AND type = ?",
        (profile_id, node_type),
    ) as cur:
        row = await cur.fetchone()
    return (row["v"] or 0) + 1


async def create_custom_node(
    db: aiosqlite.Connection, profile_id: str, node_type: str, *, name: str, description: str,
    category: str, icon: str, kind: str, manifest: dict, code: str | None,
) -> dict:
    """Insert a new version of a custom node. The version is the current max + 1,
    so an existing type is upgraded rather than replaced (old versions keep
    running until a graph migrates)."""
    version = await custom_node_next_version(db, profile_id, node_type)
    now = _now()
    node_id = uuid.uuid4().hex
    await db.execute(
        "INSERT INTO custom_nodes (id, profile_id, type, version, name, description, category, "
        "icon, kind, manifest_json, code, enabled, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (node_id, profile_id, node_type, version, name, description, category, icon, kind,
         json.dumps(manifest), code, now, now),
    )
    await db.commit()
    return await get_custom_node(db, profile_id, node_type)


async def get_custom_node(
    db: aiosqlite.Connection, profile_id: str, node_type: str, version: int | None = None,
) -> dict | None:
    """The current (highest) version of a custom node, or a specific version."""
    if version is None:
        async with db.execute(
            "SELECT * FROM custom_nodes WHERE profile_id = ? AND type = ? "
            "ORDER BY version DESC LIMIT 1",
            (profile_id, node_type),
        ) as cur:
            row = await cur.fetchone()
    else:
        async with db.execute(
            "SELECT * FROM custom_nodes WHERE profile_id = ? AND type = ? AND version = ?",
            (profile_id, node_type, version),
        ) as cur:
            row = await cur.fetchone()
    return _custom_node_row(row) if row else None


async def list_custom_nodes(db: aiosqlite.Connection, profile_id: str, *, enabled_only: bool = False) -> list[dict]:
    """The current version of every custom node the profile owns (newest first)."""
    async with db.execute(
        "SELECT * FROM custom_nodes c WHERE c.profile_id = ? AND c.version = "
        "(SELECT MAX(version) FROM custom_nodes WHERE profile_id = c.profile_id AND type = c.type) "
        "ORDER BY c.updated_at DESC",
        (profile_id,),
    ) as cur:
        rows = await cur.fetchall()
    out = [_custom_node_row(r) for r in rows]
    if enabled_only:
        out = [n for n in out if n["enabled"]]
    return out


async def list_custom_node_versions(db: aiosqlite.Connection, profile_id: str, node_type: str) -> list[dict]:
    async with db.execute(
        "SELECT * FROM custom_nodes WHERE profile_id = ? AND type = ? ORDER BY version DESC",
        (profile_id, node_type),
    ) as cur:
        rows = await cur.fetchall()
    return [_custom_node_row(r) for r in rows]


async def set_custom_node_enabled(
    db: aiosqlite.Connection, profile_id: str, node_type: str, enabled: bool,
) -> bool:
    cur = await db.execute(
        "UPDATE custom_nodes SET enabled = ?, updated_at = ? WHERE profile_id = ? AND type = ?",
        (1 if enabled else 0, _now(), profile_id, node_type),
    )
    await db.commit()
    return (cur.rowcount or 0) > 0


async def delete_custom_node(db: aiosqlite.Connection, profile_id: str, node_type: str) -> bool:
    """Delete every version of a custom node type. Callers must check dependents
    first — this does not."""
    cur = await db.execute(
        "DELETE FROM custom_nodes WHERE profile_id = ? AND type = ?", (profile_id, node_type)
    )
    await db.commit()
    return (cur.rowcount or 0) > 0


async def workflows_using_node_type(db: aiosqlite.Connection, profile_id: str, node_type: str) -> list[dict]:
    """Workflows owned by the profile whose graph references ``node_type`` — the
    dependents that block a delete (roadmap 19.2)."""
    async with db.execute(
        "SELECT id, name, graph_json FROM workflows WHERE profile_id = ?", (profile_id,)
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        try:
            graph = json.loads(r["graph_json"])
        except (ValueError, TypeError):
            continue
        if any((n or {}).get("type") == node_type for n in graph.get("nodes", [])):
            out.append({"id": r["id"], "name": r["name"]})
    return out


# ── telegram command bindings (Phase 52 / roadmap fase 20.5) ──────────────────

def _tg_binding_row(row) -> dict:
    return {
        "id": row["id"],
        "profile_id": row["profile_id"],
        "command": row["command"],
        "workflow_id": row["workflow_id"],
        "description": row["description"],
        "created_at": row["created_at"],
    }


async def create_telegram_binding(
    db: aiosqlite.Connection, profile_id: str, command: str, workflow_id: str, description: str = "",
) -> dict:
    """Bind a bot command to a workflow. Raises ``ValueError`` on a collision
    (the command is already claimed in this profile)."""
    command = command.lstrip("/").strip().lower()
    existing = await get_telegram_binding(db, profile_id, command)
    if existing is not None:
        raise ValueError(f"command '/{command}' is already bound to another workflow")
    now = _now()
    binding_id = uuid.uuid4().hex
    await db.execute(
        "INSERT INTO telegram_command_bindings (id, profile_id, command, workflow_id, description, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (binding_id, profile_id, command, workflow_id, description, now),
    )
    await db.commit()
    return await get_telegram_binding(db, profile_id, command)


async def get_telegram_binding(db: aiosqlite.Connection, profile_id: str, command: str) -> dict | None:
    command = command.lstrip("/").strip().lower()
    async with db.execute(
        "SELECT * FROM telegram_command_bindings WHERE profile_id = ? AND command = ?",
        (profile_id, command),
    ) as cur:
        row = await cur.fetchone()
    return _tg_binding_row(row) if row else None


async def find_telegram_binding_by_command(db: aiosqlite.Connection, command: str) -> dict | None:
    """Look a command up across all profiles (the bot is a single deployment).
    First match wins — collisions within a profile are already prevented."""
    command = command.lstrip("/").strip().lower()
    async with db.execute(
        "SELECT * FROM telegram_command_bindings WHERE command = ? ORDER BY created_at LIMIT 1",
        (command,),
    ) as cur:
        row = await cur.fetchone()
    return _tg_binding_row(row) if row else None


async def list_telegram_bindings(db: aiosqlite.Connection, profile_id: str) -> list[dict]:
    async with db.execute(
        "SELECT * FROM telegram_command_bindings WHERE profile_id = ? ORDER BY command",
        (profile_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_tg_binding_row(r) for r in rows]


async def list_all_telegram_bindings(db: aiosqlite.Connection) -> list[dict]:
    async with db.execute(
        "SELECT * FROM telegram_command_bindings ORDER BY command"
    ) as cur:
        rows = await cur.fetchall()
    return [_tg_binding_row(r) for r in rows]


async def delete_telegram_binding(db: aiosqlite.Connection, profile_id: str, command: str) -> bool:
    command = command.lstrip("/").strip().lower()
    cur = await db.execute(
        "DELETE FROM telegram_command_bindings WHERE profile_id = ? AND command = ?",
        (profile_id, command),
    )
    await db.commit()
    return (cur.rowcount or 0) > 0
