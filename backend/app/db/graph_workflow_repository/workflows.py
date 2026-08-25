"""The workflows table and its immutable version history.

Extracted from the former single-file graph_workflow_repository.py.
"""

import json
import uuid

import aiosqlite

from app.schemas.graph_workflows import GraphWorkflowOut, WorkflowGitSyncOut, WorkflowGraph

from ._common import _col, _now


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
