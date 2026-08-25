"""Run registry: listing, cancel/replay/retry/explain, debug stepping,
comparison, SSE streaming and the editor's last node outputs.

Extracted verbatim from the former single-file graph_workflows.py.
"""

import asyncio
import json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from app.db import audit_repository, graph_workflow_repository as repo
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.schemas.auth import UserOut
from app.schemas.graph_workflows import (
    DebugCommandIn,
    GraphRunOut,
    WorkflowExplainOut,
    RunCompareOut,
)
from app.services import workflow_graph_service as engine

from ._common import _client_ip, _owned

router = APIRouter()

# ── run registry (profile-wide) ─────────────────────────────────────────────

@router.get("/runs", response_model=list[GraphRunOut])
async def list_all_runs(
    limit: int = 100,
    status: str | None = None,
    workflow_id: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Every run of the profile (all workflows), newest first, with the workflow
    name joined — the execution registry behind the Runs view. Static path
    declared before ``/{wf_id}`` so it isn't swallowed by it."""
    return await repo.list_runs_for_profile(
        db, profile_id, limit=min(max(limit, 1), 500), status=status, workflow_id=workflow_id
    )


@router.post("/runs/{run_id}/cancel", response_model=GraphRunOut)
async def cancel_run(
    run_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Stop a queued/pending/running run. Task cancellation is asynchronous, so the
    returned row may still read 'running' for an instant — poll/SSE settles it."""
    run = await repo.get_run(db, run_id)
    if not run or run.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status not in ("queued", "pending", "running", "waiting"):
        raise HTTPException(status_code=409, detail=f"Run is already {run.status}")
    await engine.cancel_run(db, run_id)
    await audit_repository.record(
        db, user.id, "graph_workflow.run.cancel", resource=run_id, ip=_client_ip(request)
    )
    return await repo.get_run(db, run_id)


@router.post("/runs/{run_id}/replay")
async def replay_run(
    run_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Re-run the workflow with the exact trigger payload of a past run — a
    one-click reproduction for debugging. Uses the workflow's *current* graph
    (so a fix can be verified against the original input); returns the new
    run id. Partial runs can't be replayed (they have no full trigger payload)."""
    run = await repo.get_run(db, run_id)
    if not run or run.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.trigger_type == "partial":
        raise HTTPException(status_code=409, detail="Partial runs cannot be replayed")
    wf = await _owned(db, run.workflow_id, profile_id)
    ctx = await repo.get_run_context(db, run_id) or {}
    payload = ctx.get("trigger") or {}
    new_run_id = await engine.run_workflow(
        db, wf.id, profile_id, trigger_type="manual",
        trigger_payload=payload, graph=wf.graph,
        environment=run.environment, origin_run_id=run_id,
    )
    await audit_repository.record(
        db, user.id, "graph_workflow.run.replay", resource=wf.id,
        detail=run_id, ip=_client_ip(request)
    )
    return {"run_id": new_run_id}


@router.post("/runs/{run_id}/retry")
async def retry_run(
    run_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Fase 7.1 — relaunch a FAILED run from its failed node: a new run reuses
    the origin's graph snapshot and checkpointed node outputs, re-executing only
    the missing subgraph (crash-resume mechanics on explicit request). The new
    run records ``origin_run_id`` back to this one."""
    run = await repo.get_run(db, run_id)
    if not run or run.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        new_run_id = await engine.retry_run(db, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await audit_repository.record(
        db, user.id, "graph_workflow.run.retry", resource=run.workflow_id,
        detail=run_id, ip=_client_ip(request)
    )
    return {"run_id": new_run_id}


@router.post("/runs/{run_id}/explain", response_model=WorkflowExplainOut)
async def explain_run(
    run_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Roadmap fase 13.2 — "explain / repair": the failed node's catalog entry,
    input and error go to the LLM, which returns a plain-language explanation
    and (optionally) a corrected params object. Never applied automatically —
    the editor shows the diff for the user to accept or discard."""
    try:
        result = await engine.explain_run(db, profile_id, run_id)
    except ValueError as exc:
        status = 404 if str(exc) == "Run not found" else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    await audit_repository.record(
        db, user.id, "graph_workflow.run.explain", resource=run_id, ip=_client_ip(request)
    )
    return result


@router.post("/runs/{run_id}/debug")
async def debug_run(
    run_id: str,
    body: DebugCommandIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Fase 8.3 — advance a ``paused`` step-debug run: ``step`` runs the next
    node then pauses again, ``continue`` runs to the next breakpoint (or the
    end), ``stop`` cancels the run. ``breakpoints`` (optional) replaces the run's
    breakpoint set; ``input`` (optional) mocks the next node's primary input."""
    run = await repo.get_run(db, run_id)
    if not run or run.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Run not found")
    fields = body.model_dump(exclude_unset=True)
    try:
        result = await engine.debug_run(
            db, run_id, body.command,
            breakpoints=body.breakpoints, input_override=body.input,
            has_input="input" in fields,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await audit_repository.record(
        db, user.id, f"graph_workflow.debug.{body.command}", resource=run.workflow_id,
        detail=run_id, ip=_client_ip(request),
    )
    return result


@router.get("/{wf_id}/runs", response_model=list[GraphRunOut])
async def list_runs(
    wf_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await _owned(db, wf_id, profile_id)
    return await repo.list_runs(db, wf_id)


@router.get("/runs/compare", response_model=RunCompareOut)
async def compare_runs(
    a: str = Query(..., description="run id A"),
    b: str = Query(..., description="run id B"),
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Fase 17.4 — side-by-side diff of two runs of the same workflow: per-node
    status/duration/output and the first divergent node."""
    for rid in (a, b):
        run = await repo.get_run(db, rid)
        if not run or run.profile_id != profile_id:
            raise HTTPException(status_code=404, detail="Run not found")
    try:
        return await engine.compare_runs(db, a, b)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}", response_model=GraphRunOut)
async def get_run(
    run_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    run = await repo.get_run(db, run_id)
    if not run or run.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Run not found")
    run.node_runs = await repo.list_node_runs(db, run_id)
    return run


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    run = await repo.get_run(db, run_id)
    if not run or run.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Run not found")

    queue = engine.subscribe(run_id)

    async def _events():
        # Replay the current state first so a late subscriber isn't blank.
        node_runs = await repo.list_node_runs(db, run_id)
        yield {"event": "snapshot", "data": json.dumps({
            "status": run.status,
            "nodes": [{"node_id": nr.node_id, "status": nr.status} for nr in node_runs],
        })}
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
                    continue
                yield {"event": "message", "data": json.dumps(event)}
                if event.get("kind") == "done":
                    break
        finally:
            engine.unsubscribe(run_id, queue)

    return EventSourceResponse(_events())


# ── run history for the editor ──────────────────────────────────────────────

@router.get("/{wf_id}/node-outputs")
async def latest_node_outputs(
    wf_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """The most recent persisted output of every node across all past runs of
    this workflow: ``{node_id: {output, run_id, finished_at, run_created_at}}``.
    Lets the editor's edge inspector show real data from execution history."""
    await _owned(db, wf_id, profile_id)
    return await repo.latest_node_outputs(db, wf_id)
