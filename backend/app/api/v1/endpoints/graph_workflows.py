"""
Phase 29 — visual node-graph workflow endpoints.

Protected routes (under /v1/graph-workflows):
  GET    /node-types           — palette catalog (static nodes + tool.* nodes)
  GET    /schedules            — cross-workflow schedules overview (all triggers + last run)
  GET    /approvals            — human-approval requests (fase 4.4; ?status=&run_id=)
  POST   /approvals/{aid}/decision — approve/reject a pending request; the run resumes
  GET    /stats                — per-workflow metrics: runs, success rate, duration, tokens (fase 5.1)
  POST   /import               — create from a portable snapshot, with validation warnings (fase 5.2)
  POST   /generate             — natural language → validated draft graph, NOT saved (fase 5.3)
  POST   /generate/stream      — same, but streams `log` SSE events then `done`/`error`
  GET    /secrets              — profile secrets (names only, never values)
  PUT    /secrets              — upsert one secret ($secrets.<name> in expressions)
  DELETE /secrets/{name}       — remove a secret
  GET    /                     — list the profile's workflows
  POST   /                     — create a workflow
  GET    /{id}                 — one workflow (+ triggers)
  PATCH  /{id}                 — update name/description/graph (bumps version)
  DELETE /{id}                 — delete a workflow
  POST   /{id}/activate        — enable the workflow (its triggers start firing)
  POST   /{id}/deactivate      — disable it
  POST   /{id}/run             — run now (manual trigger); body = {payload}
  POST   /{id}/nodes/{nid}/test — run ONE node in isolation (fase 3.1); no run recorded
  GET    /{id}/runs            — recent runs
  GET    /{id}/node-outputs    — latest persisted output per node (all past runs)
  GET    /{id}/export          — portable JSON snapshot (re-importable via POST /)
  GET    /{id}/versions        — version history
  POST   /{id}/versions/{v}/restore — roll the graph back to a version
  POST   /{id}/triggers        — attach a schedule/webhook/event trigger
  GET    /{id}/triggers        — list triggers
  POST   /triggers/{tid}/enable|disable
  DELETE /triggers/{tid}
  POST   /runs/{rid}/cancel    — stop a pending/running run
  POST   /runs/{rid}/replay    — re-run the workflow with this run's trigger payload
  GET    /runs/{rid}           — one run with its node runs
  GET    /runs/{rid}/stream    — SSE live run view

Public route (no auth), mounted separately:
  POST   /v1/wf/hooks/{token}  — webhook trigger; the JSON body becomes $trigger
"""

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite
from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.core.config import settings
from app.data.node_catalog import node_catalog
from app.db import audit_repository, graph_workflow_repository as repo
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.examples import list_graph_workflow_examples
from app.schemas.auth import UserOut
from app.schemas.graph_workflows import (
    ApprovalDecisionIn,
    ExpressionPreviewIn,
    GraphRunOut,
    GraphWorkflowCreate,
    GraphWorkflowExample,
    GraphWorkflowOut,
    GraphWorkflowUpdate,
    NodeTestIn,
    NodeTypeInfo,
    RunTriggerIn,
    WorkflowApprovalOut,
    WorkflowGenerateIn,
    WorkflowGenerateOut,
    WorkflowImportIn,
    WorkflowImportOut,
    WorkflowStatsOut,
    WorkflowScheduleOut,
    WorkflowSecretIn,
    WorkflowSecretOut,
    WorkflowTriggerCreate,
    WorkflowTriggerOut,
)
from app.services import reminder_parsing, workflow_graph_service as engine

logger = logging.getLogger(__name__)

router = APIRouter()
public_router = APIRouter()  # unauthenticated webhook receiver


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _owned(db: aiosqlite.Connection, wf_id: str, profile_id: str) -> GraphWorkflowOut:
    wf = await repo.get_workflow(db, wf_id)
    if not wf or wf.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf


# ── palette ─────────────────────────────────────────────────────────────────

@router.get("/node-types", response_model=list[NodeTypeInfo])
async def node_types(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Palette catalog: static nodes + a ``tool.<name>`` node per built-in tool,
    plus discovered MCP server tools and the profile's custom tools."""
    return await node_catalog(db, profile_id)


@router.get("/examples", response_model=list[GraphWorkflowExample])
async def list_examples():
    """Curated, one-click-importable graph workflows. Static path declared before
    the dynamic ``/{wf_id}`` route so it isn't swallowed by it."""
    return list_graph_workflow_examples()


@router.get("/schedules", response_model=list[WorkflowScheduleOut])
async def list_schedules(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Cross-workflow schedules overview (Phase 30.e): every trigger of every
    workflow owned by the profile, with its next run and last run status —
    static path declared before ``/{wf_id}`` so it isn't swallowed by it."""
    return await repo.list_schedules_for_profile(db, profile_id)


# ── secrets (Phase 32 — roadmap fase 1) ─────────────────────────────────────
# Profile-scoped, Fernet-encrypted at rest, referenced in node params as
# ``{{ $secrets.<name> }}``. Static paths declared before ``/{wf_id}``.

@router.get("/secrets", response_model=list[WorkflowSecretOut])
async def list_secrets(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Names + timestamps only — a stored secret value is never returned."""
    return await repo.list_secrets(db, profile_id)


@router.put("/secrets", response_model=WorkflowSecretOut)
async def put_secret(
    body: WorkflowSecretIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Create or replace one secret (upsert by name)."""
    from app.services import vault_service

    encrypted = vault_service.encrypt(body.value, settings.vault_secret_key)
    await repo.upsert_secret(db, profile_id, body.name, encrypted)
    await audit_repository.record(
        db, user.id, "graph_workflow.secret.put", resource=body.name, ip=_client_ip(request)
    )
    for row in await repo.list_secrets(db, profile_id):
        if row["name"] == body.name:
            return row
    raise HTTPException(status_code=500, detail="Secret not stored")


@router.delete("/secrets/{name}", status_code=204)
async def delete_secret(
    name: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    if not await repo.delete_secret(db, profile_id, name):
        raise HTTPException(status_code=404, detail="Secret not found")
    await audit_repository.record(
        db, user.id, "graph_workflow.secret.delete", resource=name, ip=_client_ip(request)
    )


# ── stats, import & generation (Phase 36 — roadmap fase 5) ──────────────────
# Static paths declared before ``/{wf_id}`` so they aren't swallowed by it.

@router.get("/stats", response_model=list[WorkflowStatsOut])
async def workflow_stats(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Fase 5.1 — per-workflow aggregates: run counts, success rate, average
    duration and LLM token totals (from the `_usage` key of llm.* node runs)."""
    return await repo.workflow_stats_for_profile(db, profile_id)


@router.post("/import", response_model=WorkflowImportOut, status_code=201)
async def import_workflow(
    body: WorkflowImportIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Fase 5.2 — create a workflow from a portable snapshot (the body of
    GET /{id}/export) with schema validation. Unknown node types, broken edges
    and `$secrets` references missing from this profile come back as warnings
    (the workflow still imports and can be fixed in the editor)."""
    try:
        warnings = await engine.validate_import(db, profile_id, body.graph)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    wf = await repo.create_workflow(
        db, profile_id, body.name, body.description, body.graph,
        variables=body.variables, max_concurrent_runs=body.max_concurrent_runs,
    )
    await audit_repository.record(
        db, user.id, "graph_workflow.import", resource=wf.id, ip=_client_ip(request)
    )
    return WorkflowImportOut(workflow=wf, warnings=warnings)


@router.post("/generate", response_model=WorkflowGenerateOut)
async def generate_workflow(
    body: WorkflowGenerateIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Fase 5.3 — natural-language description → validated draft graph (name,
    description, nodes/edges laid out). The draft is NOT saved: the editor opens
    it for review and the user saves it explicitly."""
    try:
        draft = await engine.generate_workflow(
            db, profile_id, body.prompt, model=body.model, failover_chain=body.failover_chain
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit_repository.record(
        db, user.id, "graph_workflow.generate", resource=draft["name"], ip=_client_ip(request)
    )
    return WorkflowGenerateOut(**draft)


@router.post("/generate/stream")
async def generate_workflow_stream(
    body: WorkflowGenerateIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Fase 5.3 — the streaming twin of POST /generate: emits ``log`` SSE events
    ({step, detail}) while the draft is being produced — catalog loaded, model
    called, reply received, graph normalized, layout — then a final ``done``
    event with the draft (or ``error`` with the reason). The editor's generate
    dialog renders the log live instead of a bare spinner."""
    queue: asyncio.Queue = asyncio.Queue()

    async def _events():
        # The generation runs on its own connection: the request-scoped `db`
        # may already be torn down while this response is still streaming.
        gen_db = await engine._connect()
        task = asyncio.get_running_loop().create_task(
            engine.generate_workflow(
                gen_db, profile_id, body.prompt,
                model=body.model, failover_chain=body.failover_chain,
                on_progress=lambda step, detail: queue.put_nowait({"step": step, "detail": detail}),
            )
        )
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=0.3)
                    yield {"event": "log", "data": json.dumps(ev)}
                except asyncio.TimeoutError:
                    if task.done():
                        break
                    if await request.is_disconnected():
                        return
            while not queue.empty():
                yield {"event": "log", "data": json.dumps(queue.get_nowait())}
            try:
                draft = task.result()
            except Exception as exc:  # noqa: BLE001 — surfaced as an SSE error event
                yield {"event": "error", "data": json.dumps({"detail": str(exc)})}
                return
            await audit_repository.record(
                gen_db, user.id, "graph_workflow.generate", resource=draft["name"], ip=_client_ip(request)
            )
            yield {"event": "done", "data": WorkflowGenerateOut(**draft).model_dump_json()}
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            await gen_db.close()

    return EventSourceResponse(_events())


# ── approvals (Phase 35 — roadmap fase 4.4) ─────────────────────────────────
# Static paths declared before ``/{wf_id}`` so they aren't swallowed by it.

@router.get("/approvals", response_model=list[WorkflowApprovalOut])
async def list_approvals(
    status: str | None = "pending",
    run_id: str | None = None,
    limit: int = 100,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """The profile's human-approval requests, newest first (``status=pending``
    by default; pass ``status=`` empty for all). ``run_id`` scopes the list to
    one run — the runs page uses it to render the approve/reject panel."""
    return await repo.list_approvals(
        db, profile_id, status=status or None, run_id=run_id, limit=min(max(limit, 1), 500)
    )


@router.post("/approvals/{approval_id}/decision", response_model=WorkflowApprovalOut)
async def decide_approval(
    approval_id: str,
    body: ApprovalDecisionIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Approve or reject a pending request. The suspended run picks the decision
    up within a couple of seconds and continues down the matching branch."""
    approval = await repo.get_approval(db, approval_id)
    if not approval or approval.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail=f"Approval is already {approval.status}")
    decided = await repo.decide_approval(
        db, approval_id,
        status="approved" if body.approved else "rejected",
        decided_by=user.id, comment=body.comment,
    )
    if not decided:  # raced the engine's timeout poll — first writer wins
        raise HTTPException(status_code=409, detail="Approval was just settled")
    await audit_repository.record(
        db, user.id, "graph_workflow.approval.decide", resource=approval_id, ip=_client_ip(request)
    )
    return await repo.get_approval(db, approval_id)


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
    )
    await audit_repository.record(
        db, user.id, "graph_workflow.run.replay", resource=run_id, ip=_client_ip(request)
    )
    return {"run_id": new_run_id}


# ── workflow CRUD ───────────────────────────────────────────────────────────

@router.get("", response_model=list[GraphWorkflowOut])
async def list_workflows(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    return await repo.list_workflows(db, profile_id)


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
    )
    await audit_repository.record(db, user.id, "graph_workflow.create", resource=wf.id, ip=_client_ip(request))
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
    )
    await audit_repository.record(db, user.id, "graph_workflow.update", resource=wf_id, ip=_client_ip(request))
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
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await _owned(db, wf_id, profile_id)
    await repo.set_active(db, wf_id, True)
    wf = await _owned(db, wf_id, profile_id)
    wf.triggers = await repo.list_triggers(db, wf_id)
    return wf


@router.post("/{wf_id}/deactivate", response_model=GraphWorkflowOut)
async def deactivate(
    wf_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await _owned(db, wf_id, profile_id)
    await repo.set_active(db, wf_id, False)
    wf = await _owned(db, wf_id, profile_id)
    wf.triggers = await repo.list_triggers(db, wf_id)
    return wf


# ── running ─────────────────────────────────────────────────────────────────

@router.post("/{wf_id}/run")
async def run_now(
    wf_id: str,
    body: RunTriggerIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    wf = await _owned(db, wf_id, profile_id)
    try:
        run_id = await engine.run_workflow(
            db, wf_id, profile_id,
            trigger_type="partial" if body.start_node_id else "manual",
            trigger_payload=body.payload, graph=wf.graph,
            start_node_id=body.start_node_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_repository.record(db, user.id, "graph_workflow.run", resource=wf_id, ip=_client_ip(request))
    return {"run_id": run_id}


@router.post("/{wf_id}/nodes/{node_id}/test")
async def test_node(
    wf_id: str,
    node_id: str,
    body: NodeTestIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Roadmap fase 3.1 — execute a single node with its current (or mocked)
    input and return the output inline; no run is recorded. ``body.node``
    may carry the unsaved editor state of the node; ``body.input`` mocks
    its primary input. Node failures come back as ``{ok: false, error}``."""
    await _owned(db, wf_id, profile_id)
    if body.node is not None and body.node.id != node_id:
        raise HTTPException(status_code=400, detail="node id mismatch")
    try:
        result = await engine.test_node(
            db, wf_id, profile_id, node_id,
            node_override=body.node, input_override=body.input,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await audit_repository.record(
        db, user.id, "graph_workflow.node.test", resource=f"{wf_id}/{node_id}", ip=_client_ip(request)
    )
    return result


@router.post("/{wf_id}/preview-expression")
async def preview_expression(
    wf_id: str,
    body: ExpressionPreviewIn,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Evaluate an expression read-only against the workflow's latest run data
    (node outputs + trigger). Returns {ok, value} or {ok, error} — never 500s
    on a bad expression, so the editor can show the message inline."""
    await _owned(db, wf_id, profile_id)
    return await engine.preview_expression(db, wf_id, body.expression)


@router.get("/{wf_id}/runs", response_model=list[GraphRunOut])
async def list_runs(
    wf_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await _owned(db, wf_id, profile_id)
    return await repo.list_runs(db, wf_id)


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
        "graph": wf.graph.model_dump(),
        # $vars travel with the file (they are plain config); $secrets never do —
        # references like $secrets.NAME must be re-satisfied in the target env.
        "variables": wf.variables,
        "max_concurrent_runs": wf.max_concurrent_runs,
        # Fase 5.2 — names of the $secrets the graph references (values NEVER
        # travel): the importer knows which secrets to re-create over there.
        "secrets": engine.secret_references(wf.graph),
        "workflow_version": wf.version,
        "exported_at": int(time.time()),
    }


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


# ── triggers ────────────────────────────────────────────────────────────────

@router.get("/{wf_id}/triggers", response_model=list[WorkflowTriggerOut])
async def list_triggers(
    wf_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await _owned(db, wf_id, profile_id)
    return await repo.list_triggers(db, wf_id)


@router.post("/{wf_id}/triggers", response_model=WorkflowTriggerOut, status_code=201)
async def create_trigger(
    wf_id: str,
    body: WorkflowTriggerCreate,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await _owned(db, wf_id, profile_id)
    next_run_at = None
    config = dict(body.config)
    if body.type == "schedule":
        next_run_at, config = _resolve_schedule(config)
    trigger = await repo.create_trigger(
        db, wf_id, body.type, config, next_run_at=next_run_at, enabled=body.enabled
    )
    return trigger


_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse_hhmm(value: str | None) -> tuple[int, int]:
    m = _HHMM_RE.match(str(value or "").strip())
    if not m:
        raise HTTPException(status_code=400, detail="'time' must be HH:MM")
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        raise HTTPException(status_code=400, detail="'time' must be HH:MM")
    return hour, minute


def _next_weekday_at(now: datetime, weekday: str, hour: int, minute: int) -> datetime:
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (_WEEKDAYS.index(weekday) - now.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def _resolve_schedule(config: dict) -> tuple[int | None, dict]:
    """Normalise a schedule trigger config into ``recurrence`` + ``next_run_at``.

    Phase 30.f — the Schedules page builds a structured ``pattern``
    (daily|weekly|cron|once) instead of free natural language, so the picked
    day/time or cron expression is honoured exactly (the old ``text``/
    ``recurrence`` fields — used by the designer's quick-add and the API —
    still work unchanged for backward compatibility).
    """
    tz = ZoneInfo(getattr(settings, "timezone", None) or "UTC")
    now = datetime.now(tz)
    pattern = config.get("pattern")

    if pattern == "daily":
        hour, minute = _parse_hhmm(config.get("time"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return int(target.timestamp()), {**config, "recurrence": "daily"}

    if pattern == "weekly":
        hour, minute = _parse_hhmm(config.get("time"))
        weekdays = [d for d in (config.get("weekdays") or []) if d in _WEEKDAYS]
        if not weekdays:
            raise HTTPException(status_code=400, detail="'weekdays' must include at least one day")
        candidates = [_next_weekday_at(now, d, hour, minute) for d in weekdays]
        recurrence = "weekly:" + ",".join(weekdays)
        return int(min(candidates).timestamp()), {**config, "recurrence": recurrence}

    if pattern == "cron":
        expr = str(config.get("cron") or "").strip()
        fields = expr.split()
        if len(fields) != 5:
            raise HTTPException(status_code=400, detail="'cron' must have 5 space-separated fields")
        try:
            nxt = croniter(expr, now).get_next(datetime)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid cron expression: {exc}") from None
        return int(nxt.timestamp()), {**config, "recurrence": "cron:" + ",".join(fields)}

    if pattern == "once":
        hour, minute = _parse_hhmm(config.get("time"))
        date_str = config.get("date")
        if date_str:
            try:
                target = datetime.strptime(str(date_str), "%Y-%m-%d").replace(
                    hour=hour, minute=minute, second=0, microsecond=0, tzinfo=tz,
                )
            except ValueError:
                raise HTTPException(status_code=400, detail="'date' must be YYYY-MM-DD") from None
        else:
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
        return int(target.timestamp()), {**config, "recurrence": "once"}

    # Legacy fallback: natural-language `text` (designer's quick-add) or an
    # explicit compact `recurrence` string, as accepted since Phase 29.b.
    text = config.get("text")
    if text:
        parsed = reminder_parsing.parse_recurrence_and_when(str(text), tz)
        if parsed:
            recurrence, fire_at, _ = parsed
            return fire_at, {**config, "recurrence": recurrence}
    recurrence = config.get("recurrence", "daily")
    nxt = reminder_parsing.compute_next_fire(recurrence, int(time.time()), tz)
    return nxt, {**config, "recurrence": recurrence}


@router.post("/triggers/{tid}/enable", response_model=WorkflowTriggerOut)
async def enable_trigger(
    tid: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    trigger = await _owned_trigger(db, tid, profile_id)
    await repo.set_trigger_enabled(db, tid, True)
    return await repo.get_trigger(db, tid)


@router.post("/triggers/{tid}/disable", response_model=WorkflowTriggerOut)
async def disable_trigger(
    tid: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await _owned_trigger(db, tid, profile_id)
    await repo.set_trigger_enabled(db, tid, False)
    return await repo.get_trigger(db, tid)


@router.delete("/triggers/{tid}", status_code=204)
async def delete_trigger(
    tid: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await _owned_trigger(db, tid, profile_id)
    await repo.delete_trigger(db, tid)


@router.post("/triggers/{tid}/rotate-secret")
async def rotate_webhook_secret(
    tid: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Generate (or replace) the HMAC secret for a webhook trigger. Returned only
    once here — the caller must copy it into whatever sends the webhook (an
    `X-Signature: sha256=<hex hmac of the raw body>` header). Clearing signature
    enforcement is done by calling this with an empty body (no secret stored)."""
    trigger = await _owned_trigger(db, tid, profile_id)
    if trigger.type != "webhook":
        raise HTTPException(status_code=400, detail="Not a webhook trigger")
    secret = secrets.token_urlsafe(32)
    config = {**trigger.config, "secret": secret}
    await repo.update_trigger_config(db, tid, config)
    return {"secret": secret}


async def _owned_trigger(db: aiosqlite.Connection, tid: str, profile_id: str) -> WorkflowTriggerOut:
    trigger = await repo.get_trigger(db, tid)
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")
    wf = await repo.get_workflow(db, trigger.workflow_id)
    if not wf or wf.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return trigger


# ── public webhook receiver ─────────────────────────────────────────────────

@public_router.post("/hooks/{token}")
async def webhook(token: str, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """Public token-scoped webhook. Fires the workflow if its trigger is enabled
    and the workflow is active. The JSON body becomes ``$trigger``.

    If the trigger's config has a ``secret`` (set via the rotate-secret endpoint),
    the request must carry a matching ``X-Signature: sha256=<hex hmac of the raw
    body>`` header — a missing/incorrect signature is rejected before the body is
    ever parsed or the workflow runs."""
    trigger = await repo.get_trigger_by_token(db, token)
    if not trigger or not trigger.enabled:
        raise HTTPException(status_code=404, detail="Unknown webhook")
    wf = await repo.get_workflow(db, trigger.workflow_id)
    if not wf or not wf.active:
        raise HTTPException(status_code=404, detail="Workflow not active")

    raw_body = await request.body()
    secret = trigger.config.get("secret")
    if secret:
        signature = request.headers.get("x-signature", "")
        expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(raw_body) if raw_body else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"body": payload}
    run_id = await engine.run_workflow(
        db, wf.id, wf.profile_id, trigger_type="webhook", trigger_payload=payload, graph=wf.graph
    )
    return {"run_id": run_id}
