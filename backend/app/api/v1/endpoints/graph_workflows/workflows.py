"""Workflow CRUD, navigator, environments, per-node metrics, audit and state.

Extracted verbatim from the former single-file graph_workflows.py.
"""

import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.db import audit_repository, graph_workflow_repository as repo
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.schemas.auth import UserOut
from app.schemas.graph_workflows import (
    EnvironmentPromoteIn,
    GraphWorkflowCreate,
    GraphWorkflowOut,
    GraphWorkflowUpdate,
    WorkflowAuditEntryOut,
    WorkflowNodeStatsOut,
    WorkflowNodeVariantStatsOut,
    WorkflowStateIn,
    WorkflowStateOut,
)
from app.services import workflow_graph_service as engine

from ._common import _client_ip, _owned

router = APIRouter()

# ── workflow CRUD ───────────────────────────────────────────────────────────

@router.get("", response_model=list[GraphWorkflowOut])
async def list_workflows(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    return await repo.list_workflows(db, profile_id)


# ── workflow navigator (Phase 49 — roadmap fase 17.3) ───────────────────────
# Registered ahead of GET/{wf_id} below so "/search" and "/folders" never match
# as a workflow id.

@router.get("/search", response_model=list[GraphWorkflowOut])
async def search_workflows(
    q: str | None = Query(default=None, description="full-text over name/description/node contents"),
    folder: str | None = Query(default=None, description="exact folder match ('' = root)"),
    tag: str | None = Query(default=None, description="workflows carrying this tag"),
    include_archived: bool = Query(default=False),
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Fase 17.3 — the workflow navigator: full-text search over name, description
    AND node contents, filtered by folder/tag, archived hidden unless asked."""
    return await repo.search_workflows(
        db, profile_id, query=q, folder=folder, tag=tag, include_archived=include_archived,
    )


@router.get("/folders", response_model=list[str])
async def list_workflow_folders(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Fase 17.3 — distinct folder names for the navigator tree."""
    return await repo.list_folders(db, profile_id)


@router.post("", response_model=GraphWorkflowOut, status_code=201)
async def create_workflow(
    body: GraphWorkflowCreate,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    wf = await repo.create_workflow(
        db, profile_id, body.name, body.description, body.graph, variables=body.variables,
        max_concurrent_runs=body.max_concurrent_runs,
        input_schema=body.input_schema, output_schema=body.output_schema,
        environments=body.environments, expose_as_tool=body.expose_as_tool,
        token_budget_month=body.token_budget_month, run_budget_month=body.run_budget_month,
        runs_retention_days=body.runs_retention_days,
    )
    await audit_repository.record(db, user.id, "graph_workflow.create", resource=wf.id, ip=_client_ip(request))
    # Fase 13.3 — a brand-new workflow has no git_sync config yet, so this is a
    # no-op today; kept symmetric with update_workflow for when it's set later
    # and the workflow is re-saved.
    await engine.git_sync_push_version(db, wf, wf.version, wf.graph, user.email)
    return wf


@router.get("/{wf_id}", response_model=GraphWorkflowOut)
async def get_workflow(
    wf_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    wf = await _owned(db, wf_id, profile_id)
    wf.triggers = await repo.list_triggers(db, wf_id)
    return wf


@router.patch("/{wf_id}", response_model=GraphWorkflowOut)
async def update_workflow(
    wf_id: str,
    body: GraphWorkflowUpdate,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    await _owned(db, wf_id, profile_id)
    wf = await repo.update_workflow(
        db, wf_id, name=body.name, description=body.description, graph=body.graph,
        active=body.active, variables=body.variables,
        max_concurrent_runs=body.max_concurrent_runs,
        input_schema=body.input_schema, output_schema=body.output_schema,
        environments=body.environments, expose_as_tool=body.expose_as_tool,
        token_budget_month=body.token_budget_month, run_budget_month=body.run_budget_month,
        runs_retention_days=body.runs_retention_days,
        blackout=body.blackout, sla=body.sla, notify=body.notify,
        folder=body.folder, tags=body.tags, archived=body.archived,
    )
    await audit_repository.record(db, user.id, "graph_workflow.update", resource=wf_id, ip=_client_ip(request))
    if body.graph is not None:
        # Fase 13.3 — a new version was just snapshotted; mirror it to the
        # configured Git repo. Best-effort: never fails the save.
        await engine.git_sync_push_version(db, wf, wf.version, wf.graph, user.email)
    wf.triggers = await repo.list_triggers(db, wf_id)
    return wf


@router.delete("/{wf_id}", status_code=204)
async def delete_workflow(
    wf_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    await _owned(db, wf_id, profile_id)
    await repo.delete_workflow(db, wf_id)
    await audit_repository.record(db, user.id, "graph_workflow.delete", resource=wf_id, ip=_client_ip(request))


@router.post("/{wf_id}/activate", response_model=GraphWorkflowOut)
async def activate(
    wf_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    await _owned(db, wf_id, profile_id)
    await repo.set_active(db, wf_id, True)
    await audit_repository.record(db, user.id, "graph_workflow.activate", resource=wf_id, ip=_client_ip(request))
    wf = await _owned(db, wf_id, profile_id)
    wf.triggers = await repo.list_triggers(db, wf_id)
    return wf


@router.post("/{wf_id}/deactivate", response_model=GraphWorkflowOut)
async def deactivate(
    wf_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    await _owned(db, wf_id, profile_id)
    await repo.set_active(db, wf_id, False)
    await audit_repository.record(db, user.id, "graph_workflow.deactivate", resource=wf_id, ip=_client_ip(request))
    wf = await _owned(db, wf_id, profile_id)
    wf.triggers = await repo.list_triggers(db, wf_id)
    return wf


# ── environments (Phase 39 — roadmap fase 7.2) ──────────────────────────────

@router.post("/{wf_id}/environments/{env}/promote", response_model=GraphWorkflowOut)
async def promote_environment(
    wf_id: str,
    env: str,
    body: EnvironmentPromoteIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Pin a graph version to a named environment ("promote to prod"): runs in
    that environment execute the pinned version while the editor keeps working
    on the current graph. ``version`` omitted = the current version. The
    environment is created on first promote if it doesn't exist yet."""
    wf = await _owned(db, wf_id, profile_id)
    version = body.version if body.version is not None else wf.version
    if await repo.get_version_graph(db, wf_id, version) is None:
        raise HTTPException(status_code=404, detail=f"Version {version} not found")
    environments = dict(wf.environments or {})
    env_cfg = environments.get(env)
    env_cfg = dict(env_cfg) if isinstance(env_cfg, dict) else {}
    env_cfg["version"] = version
    environments[env] = env_cfg
    wf = await repo.update_workflow(db, wf_id, environments=environments)
    await audit_repository.record(
        db, user.id, "graph_workflow.environment.promote", resource=wf_id,
        detail=f"{env}@v{version}", ip=_client_ip(request)
    )
    wf.triggers = await repo.list_triggers(db, wf_id)
    return wf


# ── per-node metrics & audit (Phase 39 — roadmap fasi 7.4 / 7.3) ───────────

@router.get("/{wf_id}/stats/nodes", response_model=list[WorkflowNodeStatsOut])
async def node_stats(
    wf_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Fase 7.4 — per-node aggregates over the workflow's run history (counts by
    outcome, error rate, p50/p95 duration, tokens): the Health tab's data."""
    await _owned(db, wf_id, profile_id)
    return await repo.node_stats_for_workflow(db, wf_id)


@router.get("/{wf_id}/nodes/{node_id}/variants", response_model=list[WorkflowNodeVariantStatsOut])
async def node_variant_stats(
    wf_id: str,
    node_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Fase 18.2 — per-variant breakdown for one A/B-tested node (executions,
    ok-rate, mean llm.judge score, pass-rate, tokens), best variant flagged as
    ``winner`` so the editor can offer "promote variant"."""
    await _owned(db, wf_id, profile_id)
    return await repo.variant_stats_for_node(db, wf_id, node_id)


@router.get("/{wf_id}/audit", response_model=list[WorkflowAuditEntryOut])
async def workflow_audit(
    wf_id: str,
    limit: int = 100,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Fase 7.3 — the workflow's audit trail (who created/modified/activated/
    executed/approved what and when), newest first."""
    await _owned(db, wf_id, profile_id)
    entries = await audit_repository.list_for_resource(
        db, wf_id, limit=min(max(limit, 1), 500)
    )
    return [
        WorkflowAuditEntryOut(
            id=e.id, user_id=e.user_id, action=e.action,
            resource=e.resource, detail=e.detail, created_at=e.created_at,
        )
        for e in entries
    ]


# ── persistent state (Phase 48 — roadmap fase 16.1) ─────────────────────────
# The workflow's cross-run key/value store, viewable and editable from the run
# panel. Manual edits are audited (fase 7.3); state is never part of an export.

@router.get("/{wf_id}/state", response_model=list[WorkflowStateOut])
async def list_state(
    wf_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Fase 16.1 — every live (non-expired) persistent-state key of the workflow."""
    await _owned(db, wf_id, profile_id)
    return [WorkflowStateOut(**row) for row in await repo.state_list(db, wf_id)]


@router.put("/{wf_id}/state/{key}", response_model=WorkflowStateOut)
async def put_state(
    wf_id: str,
    key: str,
    body: WorkflowStateIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Fase 16.1 — set/overwrite one state key by hand (the audit records the edit)."""
    await _owned(db, wf_id, profile_id)
    expires = await repo.state_set(db, wf_id, key, body.value, body.ttl_seconds)
    await audit_repository.record(
        db, user.id, "graph_workflow.state.put", resource=wf_id, detail=key, ip=_client_ip(request)
    )
    found, value = await repo.state_get(db, wf_id, key)
    return WorkflowStateOut(key=key, value=value if found else body.value, expires_at=expires, updated_at=int(time.time()))


@router.delete("/{wf_id}/state/{key}", status_code=204)
async def delete_state(
    wf_id: str,
    key: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Fase 16.1 — remove one state key by hand."""
    await _owned(db, wf_id, profile_id)
    if not await repo.state_delete(db, wf_id, key):
        raise HTTPException(status_code=404, detail="State key not found")
    await audit_repository.record(
        db, user.id, "graph_workflow.state.delete", resource=wf_id, detail=key, ip=_client_ip(request)
    )
