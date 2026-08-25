"""Portable export, version history/diff/restore and git sync.

Extracted verbatim from the former single-file graph_workflows.py.
"""

import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.db import audit_repository, graph_workflow_repository as repo
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.schemas.auth import UserOut
from app.schemas.graph_workflows import (
    WorkflowGitSyncIn,
    GitSyncPullOut,
    GraphWorkflowOut,
    VersionDiffOut,
)
from app.services import workflow_graph_service as engine

from ._common import _client_ip, _owned

router = APIRouter()

# ── export ──────────────────────────────────────────────────────────────────

@router.get("/{wf_id}/export")
async def export_workflow(
    wf_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """A portable JSON snapshot of the workflow (name, description, graph).
    Re-importable via POST /v1/graph-workflows with the same body shape."""
    wf = await _owned(db, wf_id, profile_id)
    await audit_repository.record(
        db, user.id, "graph_workflow.export", resource=wf_id, ip=_client_ip(request)
    )
    return {
        "kind": "spice-sibyl.graph-workflow",
        "schema_version": 1,
        "name": wf.name,
        "description": wf.description,
        # Fase 12.2 — pinned node outputs go through their own redact paths;
        # everything else in the definition is plain portable config.
        "graph": engine.redact_graph_for_export(wf.graph),
        # $vars travel with the file (they are plain config); $secrets never do —
        # references like $secrets.NAME must be re-satisfied in the target env.
        "variables": wf.variables,
        "max_concurrent_runs": wf.max_concurrent_runs,
        # Fase 6.4 — contracts are portable config and travel with the file.
        "input_schema": wf.input_schema,
        "output_schema": wf.output_schema,
        # Fase 9.1 — the tool-exposure flag is portable config and travels too
        # (the imported copy is inactive, so it publishes nothing until activated).
        "expose_as_tool": wf.expose_as_tool,
        # Fase 7.2 — environments travel too: their vars and secret ALIASES are
        # plain config (secret values never leave; pinned versions don't apply
        # in the target env until re-promoted there).
        "environments": wf.environments,
        # Fase 5.2 — names of the $secrets the graph references (values NEVER
        # travel): the importer knows which secrets to re-create over there.
        "secrets": engine.secret_references(wf.graph),
        # Fase 19.5 — the custom-node packages the graph depends on {type, version}.
        # The importer warns when they're missing (same toast path as $secrets) and
        # offers a one-click install when the package is available in the workspace.
        "custom_nodes": await _custom_node_dependencies(db, profile_id, wf.graph),
        "workflow_version": wf.version,
        "exported_at": int(time.time()),
    }


async def _custom_node_dependencies(db, profile_id: str, graph) -> list[dict]:
    """The distinct ``custom.*`` node types a graph uses, each with the currently
    installed version (or null when the package isn't present here)."""
    types = {n.type for n in graph.nodes if n.type.startswith("custom.")}
    out = []
    for t in sorted(types):
        node = await repo.get_custom_node(db, profile_id, t)
        out.append({"type": t, "version": node["version"] if node else None})
    return out


# ── versions ────────────────────────────────────────────────────────────────

@router.get("/{wf_id}/versions")
async def list_versions(
    wf_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await _owned(db, wf_id, profile_id)
    return await repo.list_versions(db, wf_id)


@router.post("/{wf_id}/versions/{version}/restore", response_model=GraphWorkflowOut)
async def restore_version(
    wf_id: str,
    version: int,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await _owned(db, wf_id, profile_id)
    graph = await repo.get_version_graph(db, wf_id, version)
    if graph is None:
        raise HTTPException(status_code=404, detail="Version not found")
    wf = await repo.update_workflow(db, wf_id, graph=graph)
    wf.triggers = await repo.list_triggers(db, wf_id)
    return wf


@router.get("/{wf_id}/versions/{from_version}/diff/{to_version}", response_model=VersionDiffOut)
async def diff_versions(
    wf_id: str,
    from_version: int,
    to_version: int,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Fase 8.1 — structural diff between two saved versions: nodes grouped as
    added / removed / changed / unchanged (+ edge deltas) so the editor can paint
    the target canvas and show the per-node config diff."""
    await _owned(db, wf_id, profile_id)
    try:
        return await engine.diff_versions(db, wf_id, from_version, to_version)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{wf_id}/git-sync", response_model=GraphWorkflowOut)
async def set_git_sync(
    wf_id: str,
    body: WorkflowGitSyncIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Roadmap fase 13.3 — configure (or, with an empty ``repo_url``, disable)
    Git sync for this workflow. Every subsequent saved version is committed as
    JSON to the repo; ``token_secret`` names an existing $secrets entry (its
    value is never accepted here or returned)."""
    await _owned(db, wf_id, profile_id)
    await repo.set_git_sync(
        db, wf_id,
        repo_url=body.repo_url, branch=body.branch,
        token_secret=body.token_secret, subpath=body.subpath,
    )
    await audit_repository.record(
        db, user.id, "graph_workflow.git_sync.set", resource=wf_id, ip=_client_ip(request)
    )
    wf = await _owned(db, wf_id, profile_id)
    wf.triggers = await repo.list_triggers(db, wf_id)
    return wf


@router.post("/{wf_id}/git-sync/pull", response_model=GitSyncPullOut)
async def pull_git_sync(
    wf_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Roadmap fase 13.3 — pull the configured repo/branch; a changed
    definition becomes a new DRAFT version (never overwrites the live graph),
    ready to review/restore like any other version."""
    wf = await _owned(db, wf_id, profile_id)
    try:
        result = await engine.git_sync_pull(db, wf)
    except (ValueError, engine.GitSyncError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await audit_repository.record(
        db, user.id, "graph_workflow.git_sync.pull", resource=wf_id, ip=_client_ip(request)
    )
    return result
