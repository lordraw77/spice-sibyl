"""
Phase 29 — visual node-graph workflow endpoints.

Protected routes (under /v1/graph-workflows):
  GET    /node-types           — palette catalog (static nodes + tool.* nodes)
  GET    /                     — list the profile's workflows
  POST   /                     — create a workflow
  GET    /{id}                 — one workflow (+ triggers)
  PATCH  /{id}                 — update name/description/graph (bumps version)
  DELETE /{id}                 — delete a workflow
  POST   /{id}/activate        — enable the workflow (its triggers start firing)
  POST   /{id}/deactivate      — disable it
  POST   /{id}/run             — run now (manual trigger); body = {payload}
  GET    /{id}/runs            — recent runs
  GET    /{id}/versions        — version history
  POST   /{id}/versions/{v}/restore — roll the graph back to a version
  POST   /{id}/triggers        — attach a schedule/webhook/event trigger
  GET    /{id}/triggers        — list triggers
  POST   /triggers/{tid}/enable|disable
  DELETE /triggers/{tid}
  GET    /runs/{rid}           — one run with its node runs
  GET    /runs/{rid}/stream    — SSE live run view

Public route (no auth), mounted separately:
  POST   /v1/wf/hooks/{token}  — webhook trigger; the JSON body becomes $trigger
"""

import asyncio
import json
import logging
import time
from zoneinfo import ZoneInfo

import aiosqlite
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
    GraphRunOut,
    GraphWorkflowCreate,
    GraphWorkflowExample,
    GraphWorkflowOut,
    GraphWorkflowUpdate,
    NodeTypeInfo,
    RunTriggerIn,
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
    wf = await repo.create_workflow(db, profile_id, body.name, body.description, body.graph)
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
        db, wf_id, name=body.name, description=body.description, graph=body.graph, active=body.active
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
    run_id = await engine.run_workflow(
        db, wf_id, profile_id, trigger_type="manual", trigger_payload=body.payload, graph=wf.graph
    )
    await audit_repository.record(db, user.id, "graph_workflow.run", resource=wf_id, ip=_client_ip(request))
    return {"run_id": run_id}


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


def _resolve_schedule(config: dict) -> tuple[int | None, dict]:
    """Normalise a schedule trigger config into ``recurrence`` + ``next_run_at``.

    Accepts either a natural-language ``text`` (parsed via reminder_parsing) or an
    explicit ``recurrence`` (once|daily|weekly:..|cron:..). Reuses the Phase 23.d
    parser so cron/RRULE/NL support comes for free.
    """
    tz = ZoneInfo(getattr(settings, "timezone", None) or "UTC")
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
    and the workflow is active. The JSON body becomes ``$trigger``."""
    trigger = await repo.get_trigger_by_token(db, token)
    if not trigger or not trigger.enabled:
        raise HTTPException(status_code=404, detail="Unknown webhook")
    wf = await repo.get_workflow(db, trigger.workflow_id)
    if not wf or not wf.active:
        raise HTTPException(status_code=404, detail="Workflow not active")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"body": payload}
    run_id = await engine.run_workflow(
        db, wf.id, wf.profile_id, trigger_type="webhook", trigger_payload=payload, graph=wf.graph
    )
    return {"run_id": run_id}
