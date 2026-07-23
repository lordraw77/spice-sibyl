"""
Phase 29 — visual node-graph workflow endpoints.

Protected routes (under /v1/graph-workflows):
  GET    /node-types           — palette catalog (static nodes + tool.* nodes)
  GET    /schedules            — cross-workflow schedules overview (all triggers + last run)
  GET    /approvals            — human-in-the-loop requests: approval|input|event (fase 4.4, 10; ?status=&run_id=&kind=)
  POST   /approvals/{aid}/decision — approve/reject a pending human.approval request; the run resumes
  POST   /approvals/{aid}/submit — submit the form of a pending human.input request (fase 10.1)
  POST   /events/{correlation_id} — deliver an event to a suspended wait.event node (fase 10.2)
  GET    /stats                — per-workflow metrics: runs, success rate, duration, tokens (fase 5.1; ?environment= scopes to one named environment, fase 7.2)
  POST   /import               — create from a portable snapshot, with validation warnings (fase 5.2)
  POST   /generate             — natural language → validated draft graph, NOT saved (fase 5.3)
  POST   /generate/stream      — same, but streams `log` SSE events then `done`/`error`
  GET    /secrets              — profile secrets (names only, never values)
  PUT    /secrets              — upsert one secret ($secrets.<name> in expressions)
  DELETE /secrets/{name}       — remove a secret
  GET    /                     — list the profile's workflows
  GET    /search               — navigator: full-text (name/desc/nodes) + folder/tag/archived filters (fase 17.3)
  GET    /folders              — distinct folder names for the navigator tree (fase 17.3)
  POST   /                     — create a workflow
  GET    /{id}                 — one workflow (+ triggers)
  PATCH  /{id}                 — update name/description/graph (bumps version)
  DELETE /{id}                 — delete a workflow
  POST   /{id}/activate        — enable the workflow (its triggers start firing)
  POST   /{id}/deactivate      — disable it
  POST   /{id}/run             — run now (manual trigger); body = {payload}; debug=true starts a paused step-debug run (fase 8.3)
  POST   /{id}/nodes/{nid}/test — run ONE node in isolation (fase 3.1); no run recorded
  GET    /{id}/runs            — recent runs
  GET    /{id}/node-outputs    — latest persisted output per node (all past runs)
  GET    /{id}/export          — portable JSON snapshot (re-importable via POST /)
  GET    /{id}/versions        — version history
  POST   /{id}/versions/{v}/restore — roll the graph back to a version
  GET    /{id}/versions/{a}/diff/{b} — structural diff between two versions (fase 8.1)
  POST   /{id}/triggers        — attach a schedule/webhook/event trigger
  GET    /{id}/triggers        — list triggers
  POST   /triggers/{tid}/enable|disable
  DELETE /triggers/{tid}
  POST   /runs/{rid}/cancel    — stop a pending/running run
  POST   /runs/{rid}/replay    — re-run the workflow with this run's trigger payload
  POST   /runs/{rid}/retry     — relaunch a FAILED run from its failed node (fase 7.1)
  POST   /runs/{rid}/debug     — advance a paused step-debug run: step|continue|stop (fase 8.3)
  GET    /runs/compare         — diff two runs of one workflow: per-node status/duration/output + first divergent node (fase 17.4)
  GET    /runs/{rid}           — one run with its node runs
  GET    /runs/{rid}/stream    — SSE live run view
  GET    /{id}/stats/nodes     — per-node health metrics (fase 7.4)
  GET    /{id}/nodes/{nid}/variants — per-variant A/B breakdown for one node (fase 18.2)
  GET    /{id}/audit           — the workflow's audit trail (fase 7.3)
  POST   /{id}/environments/{env}/promote — pin a graph version to an environment (fase 7.2)
  GET    /{id}/test-cases      — list saved regression test cases (fase 11.1)
  POST   /{id}/test-cases      — save a test case (fixture $trigger + assertions)
  PUT    /{id}/test-cases/{cid} — update a test case
  DELETE /{id}/test-cases/{cid} — remove a test case
  POST   /{id}/test-cases/run  — run every saved test case ("Run tests")
  POST   /{id}/dry-run         — simulate the whole graph; external nodes mocked (fase 11.2)
  GET    /{id}/cost-estimate   — static tokens/month projection from stats + schedule (fase 11.3)
  POST   /runners              — provision a remote runner slot, returns a one-time token (fase 14.1)
  GET    /runners              — list this profile's runners (online/offline, labels, version)
  DELETE /runners/{rid}        — revoke a runner's token

Public routes (no user auth), mounted separately:
  POST   /v1/wf/hooks/{token}          — webhook trigger; the JSON body becomes $trigger
  POST   /v1/wf/runners/heartbeat      — runner self-report; authenticated by X-Runner-Token (fase 14.1)
  GET    /v1/wf/runners/jobs/next      — long-poll for the next job assigned to this runner
  POST   /v1/wf/runners/jobs/{jid}/result — post back {ok, output, handles, logs} (test_node() contract)
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
import httpx
from croniter import croniter
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
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
    CustomNodeEnableIn,
    CustomNodeInstallIn,
    CustomNodeOut,
    DebugCommandIn,
    EnvironmentPromoteIn,
    EventDeliverIn,
    ExposedWorkflowToolOut,
    ExpressionPreviewIn,
    GraphRunOut,
    HumanInputSubmitIn,
    OpenApiImportIn,
    OpenApiImportOut,
    ProfileBudgetIn,
    ProfileBudgetOut,
    RunnerHeartbeatIn,
    RunnerJobOut,
    RunnerJobResultIn,
    RunnerOut,
    RunnerRegisterIn,
    RunnerRegisterOut,
    WorkflowBudgetStatusOut,
    WorkflowChatIn,
    WorkflowChatOut,
    WorkflowCostEstimateOut,
    WorkflowDryRunIn,
    WorkflowDryRunOut,
    WorkflowExplainOut,
    WorkflowGitSyncIn,
    GitSyncPullOut,
    WorkflowTestCaseIn,
    WorkflowTestCaseOut,
    GraphWorkflowCreate,
    GraphWorkflowExample,
    GraphWorkflowOut,
    GraphWorkflowUpdate,
    NodeTestIn,
    NodeTypeInfo,
    RunCompareOut,
    RunTriggerIn,
    TestSuiteRunOut,
    WorkflowApprovalOut,
    WorkflowAuditEntryOut,
    WorkflowNodeStatsOut,
    WorkflowNodeVariantStatsOut,
    WorkflowGenerateIn,
    WorkflowGenerateOut,
    WorkflowImportIn,
    WorkflowImportOut,
    WorkflowStatsOut,
    WorkflowScheduleOut,
    WorkflowSecretIn,
    WorkflowSecretOut,
    WorkflowStateIn,
    WorkflowStateOut,
    WorkflowTriggerCreate,
    WorkflowTriggerOut,
    TelegramBindingIn,
    TelegramBindingOut,
    VersionDiffOut,
)
from app.services import custom_node_service, reminder_parsing, workflow_graph_service as engine

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
    environment: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Fase 5.1 — per-workflow aggregates: run counts, success rate, average
    duration and LLM token totals (from the `_usage` key of llm.* node runs).
    ``environment`` (fase 7.2, extending fase 5.1) optionally scopes every
    aggregate to runs executed in that named environment — e.g. checking
    `prod` health separately from the unfiltered (all-environments) totals."""
    return await repo.workflow_stats_for_profile(db, profile_id, environment=environment or None)


# ── Phase 44 — budgets and quotas (roadmap fase 12.1) ───────────────────────
# Static path, declared before the dynamic ``/{wf_id}`` route.

@router.get("/budget", response_model=ProfileBudgetOut)
async def get_profile_budget(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """The profile-wide ("workspace") LLM token / run cap for the calendar
    month, on top of any per-workflow cap — the caller is gated by whichever
    of the two is tighter."""
    budget = await repo.get_profile_budget(db, profile_id)
    return ProfileBudgetOut(
        profile_id=profile_id,
        token_budget_month=budget["token_budget_month"] if budget else None,
        run_budget_month=budget["run_budget_month"] if budget else None,
    )


@router.put("/budget", response_model=ProfileBudgetOut)
async def put_profile_budget(
    body: ProfileBudgetIn,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),  # noqa: ARG001 — enforce auth
):
    await repo.set_profile_budget(db, profile_id, body.token_budget_month, body.run_budget_month)
    return ProfileBudgetOut(
        profile_id=profile_id,
        token_budget_month=body.token_budget_month,
        run_budget_month=body.run_budget_month,
    )


# ── custom nodes (Phase 51 / roadmap fase 19) ────────────────────────────────

def _custom_node_out(node: dict, *, with_code: bool = False) -> CustomNodeOut:
    return CustomNodeOut(
        id=node["id"], type=node["type"], version=node["version"], name=node["name"],
        description=node["description"], category=node["category"], icon=node["icon"],
        kind=node["kind"], manifest=node["manifest"],
        code=node["code"] if with_code else None,
        enabled=node["enabled"], created_at=node["created_at"], updated_at=node["updated_at"],
    )


@router.get("/custom-nodes", response_model=list[CustomNodeOut])
async def list_custom_nodes(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """The current version of every custom node the profile has installed
    (fase 19.2). Static path declared before ``/{wf_id}`` so it isn't swallowed."""
    return [_custom_node_out(n) for n in await repo.list_custom_nodes(db, profile_id)]


@router.post("/custom-nodes", response_model=CustomNodeOut, status_code=201)
async def install_custom_node(
    body: CustomNodeInstallIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Install (or upgrade) a custom node from a manifest + optional code
    (fase 19.1/19.3). A python node's code is always run in the sandbox; a
    validation failure is a 400. Install is audited (fase 7.3)."""
    try:
        node = await custom_node_service.install(db, profile_id, body.manifest, body.code, body.signature)
    except custom_node_service.CustomNodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_repository.record(
        db, user.id, "custom_node.install", resource=node["type"], ip=_client_ip(request),
        detail=f"v{node['version']} ({node['kind']})",
    )
    return _custom_node_out(node, with_code=True)


@router.get("/custom-nodes/{node_type}", response_model=CustomNodeOut)
async def get_custom_node(
    node_type: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    node = await repo.get_custom_node(db, profile_id, node_type)
    if node is None:
        raise HTTPException(status_code=404, detail="Custom node not found")
    return _custom_node_out(node, with_code=True)


@router.get("/custom-nodes/{node_type}/versions", response_model=list[CustomNodeOut])
async def list_custom_node_versions(
    node_type: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    return [_custom_node_out(n) for n in await repo.list_custom_node_versions(db, profile_id, node_type)]


@router.post("/custom-nodes/{node_type}/versions", response_model=CustomNodeOut, status_code=201)
async def add_custom_node_version(
    node_type: str,
    body: CustomNodeInstallIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Upload a new version of an existing custom node type (fase 19.2). The
    manifest's ``type`` must match the path; older versions keep running."""
    if str(body.manifest.get("type") or "") != node_type:
        raise HTTPException(status_code=400, detail="manifest.type must match the path")
    try:
        node = await custom_node_service.install(db, profile_id, body.manifest, body.code, body.signature)
    except custom_node_service.CustomNodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_repository.record(
        db, user.id, "custom_node.version", resource=node["type"], ip=_client_ip(request),
        detail=f"v{node['version']}",
    )
    return _custom_node_out(node, with_code=True)


@router.patch("/custom-nodes/{node_type}", response_model=CustomNodeOut)
async def set_custom_node_enabled(
    node_type: str,
    body: CustomNodeEnableIn,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),  # noqa: ARG001 — enforce auth
):
    if not await repo.set_custom_node_enabled(db, profile_id, node_type, body.enabled):
        raise HTTPException(status_code=404, detail="Custom node not found")
    node = await repo.get_custom_node(db, profile_id, node_type)
    return _custom_node_out(node)


@router.delete("/custom-nodes/{node_type}", status_code=204)
async def delete_custom_node(
    node_type: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Delete every version of a custom node type. Blocked with a 409 (and the
    list of dependents) while any workflow still references it (fase 19.2)."""
    if await repo.get_custom_node(db, profile_id, node_type) is None:
        raise HTTPException(status_code=404, detail="Custom node not found")
    dependents = await custom_node_service.delete(db, profile_id, node_type)
    if dependents:
        raise HTTPException(
            status_code=409,
            detail={"message": "Custom node is still used by workflows", "dependents": dependents},
        )
    await audit_repository.record(
        db, user.id, "custom_node.delete", resource=node_type, ip=_client_ip(request)
    )
    return Response(status_code=204)


# ── telegram command bindings (Phase 52 / roadmap fase 20.5) ──────────────────

@router.get("/telegram-bindings", response_model=list[TelegramBindingOut])
async def list_telegram_bindings(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    return [TelegramBindingOut(**b) for b in await repo.list_telegram_bindings(db, profile_id)]


@router.post("/telegram-bindings", response_model=TelegramBindingOut, status_code=201)
async def create_telegram_binding(
    body: TelegramBindingIn,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),  # noqa: ARG001 — enforce auth
):
    """Bind a bot command (``/report``) to a workflow (fase 20.5). A command
    already claimed in this profile is a 409."""
    await _owned(db, body.workflow_id, profile_id)
    try:
        binding = await repo.create_telegram_binding(
            db, profile_id, body.command, body.workflow_id, body.description
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return TelegramBindingOut(**binding)


@router.delete("/telegram-bindings/{command}", status_code=204)
async def delete_telegram_binding(
    command: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),  # noqa: ARG001 — enforce auth
):
    if not await repo.delete_telegram_binding(db, profile_id, command):
        raise HTTPException(status_code=404, detail="Binding not found")
    return Response(status_code=204)


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
        input_schema=body.input_schema, output_schema=body.output_schema,
        environments=body.environments, expose_as_tool=body.expose_as_tool,
    )
    await audit_repository.record(
        db, user.id, "graph_workflow.import", resource=wf.id, ip=_client_ip(request)
    )
    return WorkflowImportOut(workflow=wf, warnings=warnings)


# ── Phase 41 — workflows as ecosystem tools (roadmap fase 9) ────────────────
# Static paths, declared before the dynamic ``/{wf_id}`` route.

@router.get("/tools", response_model=list[ExposedWorkflowToolOut])
async def list_workflow_tools(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Fase 9.1 — the profile's workflows published as callable tools (active +
    input contract + expose_as_tool), with the tool name/description/params
    derived from each contract. The same surface the MCP server (9.2) exposes."""
    from app.services import workflow_tool_service

    return await workflow_tool_service.list_descriptors(db, profile_id)


@router.post("/openapi/import", response_model=OpenApiImportOut)
async def import_openapi(
    body: OpenApiImportIn,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),  # noqa: ARG001 — enforce auth
):
    """Fase 9.4 — parse an OpenAPI spec (inline ``spec`` or by ``url``) into
    preconfigured ``http.request`` node drafts, one per operation. Nothing is
    saved: the editor drops the returned nodes onto the canvas."""
    from app.services import openapi_import_service as openapi

    spec = body.spec
    if spec is None:
        if not body.url:
            raise HTTPException(status_code=400, detail="Provide either 'spec' or 'url'.")
        try:
            spec = await openapi.fetch_spec(body.url)
        except (ValueError, httpx.HTTPError, OSError) as exc:
            raise HTTPException(status_code=400, detail=f"Could not load spec: {exc}") from exc
    try:
        title, base_url, operations, warnings = openapi.build_operations(spec, body.path_prefix)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return OpenApiImportOut(
        api_title=title, base_url=base_url, operations=operations, warnings=warnings
    )


@router.post("/mcp")
async def workflow_mcp_endpoint(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),  # noqa: ARG001 — auth = the MCP credential
):
    """Fase 9.2 — the product's workflow MCP server. A JSON-RPC 2.0 endpoint
    (streamable-HTTP transport) that publishes this profile's ``expose_as_tool``
    workflows to external MCP clients: ``initialize`` / ``tools/list`` /
    ``tools/call`` / ``ping``. A ``tools/call`` runs the workflow inline (origin
    ``mcp``) and returns its output as MCP ``content``. The raw JSON-RPC body is
    read directly (single message or batch) — see ``McpRpcIn`` for its shape."""
    from app.services import workflow_mcp_service as mcp

    try:
        raw = await request.json()
    except (ValueError, json.JSONDecodeError):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
    result = await mcp.handle_message(db, profile_id, raw)
    if result is None:
        return Response(status_code=202)
    return result


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
    kind: str | None = None,
    limit: int = 100,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """The profile's human-in-the-loop requests, newest first (``status=pending``
    by default; pass ``status=`` empty for all). ``run_id`` scopes the list to
    one run — the runs page uses it to render the approve/reject/submit panel.
    ``kind`` (fase 10) scopes to approval|input|event."""
    return await repo.list_approvals(
        db, profile_id, status=status or None, run_id=run_id, kind=kind or None, limit=min(max(limit, 1), 500)
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
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.profile_id != profile_id:
        # Fase 7.3 — a workspace member holding the 'approver' share role on the
        # workflow may decide its approvals even without owning it.
        from app.db import workspace_repository

        role = await workspace_repository.get_workflow_share_role(db, approval.workflow_id, user.id)
        if role != "approver":
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


# ── human input & wait.event (Phase 42 — roadmap fase 10) ──────────────────

@router.post("/approvals/{approval_id}/submit", response_model=WorkflowApprovalOut)
async def submit_human_input(
    approval_id: str,
    body: HumanInputSubmitIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Submit the form of a pending ``human.input`` request (fase 10.1). ``data``
    is validated against the request's JSON Schema before it is accepted; the
    suspended run picks it up within a couple of seconds and continues."""
    approval = await repo.get_approval(db, approval_id)
    if not approval or approval.kind != "input":
        raise HTTPException(status_code=404, detail="Input request not found")
    if approval.profile_id != profile_id:
        from app.db import workspace_repository

        role = await workspace_repository.get_workflow_share_role(db, approval.workflow_id, user.id)
        if role != "approver":
            raise HTTPException(status_code=404, detail="Input request not found")
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail=f"Request is already {approval.status}")
    if approval.form_schema:
        violations = engine._validate_json_schema(body.data, approval.form_schema)
        if violations:
            raise HTTPException(
                status_code=422, detail="Data does not match the requested schema: " + "; ".join(violations[:5])
            )
    decided = await repo.decide_approval(
        db, approval_id, status="submitted", decided_by=user.id, comment=body.comment, data=body.data,
    )
    if not decided:  # raced the engine's timeout poll — first writer wins
        raise HTTPException(status_code=409, detail="Request was just settled")
    await audit_repository.record(
        db, user.id, "graph_workflow.input.submit", resource=approval_id, ip=_client_ip(request)
    )
    return await repo.get_approval(db, approval_id)


@router.post("/events/{correlation_id}", response_model=WorkflowApprovalOut)
async def deliver_event(
    correlation_id: str,
    body: EventDeliverIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Deliver an external event to a suspended ``wait.event`` node (fase 10.2).
    ``payload`` becomes the node's output; the run picks it up within a couple
    of seconds and continues down 'main'."""
    approval = await repo.get_pending_event(db, correlation_id, profile_id)
    if not approval:
        raise HTTPException(status_code=404, detail="No pending wait.event for this correlation id")
    decided = await repo.decide_approval(
        db, approval.id, status="delivered", decided_by=user.id, data=body.payload,
    )
    if not decided:  # raced the engine's timeout poll — first writer wins
        raise HTTPException(status_code=409, detail="Request was just settled")
    await audit_repository.record(
        db, user.id, "graph_workflow.event.deliver", resource=correlation_id, ip=_client_ip(request)
    )
    return await repo.get_approval(db, approval.id)


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


# ── remote runners (Phase 46 — roadmap fase 14.1) ───────────────────────────
# Registered ahead of GET/{wf_id} below so "/runners" never matches as a
# workflow id.

@router.post("/runners", response_model=RunnerRegisterOut, status_code=201)
async def register_runner(
    body: RunnerRegisterIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Provision a new remote-runner slot. The raw token is returned ONLY here
    — start the agent process with it (e.g. ``SIBYL_RUNNER_TOKEN=... python -m
    app.runner.agent``); it is never retrievable again (only its hash is kept)."""
    runner_id, token = await repo.create_runner(
        db, profile_id, body.name, body.labels, body.allowed_node_types,
    )
    await audit_repository.record(
        db, user.id, "graph_workflow.runner.register", resource=runner_id, ip=_client_ip(request)
    )
    return RunnerRegisterOut(id=runner_id, token=token)


@router.get("/runners", response_model=list[RunnerOut])
async def list_runners(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    return await repo.list_runners(
        db, profile_id, heartbeat_timeout=settings.graph_workflow_runner_heartbeat_timeout,
    )


@router.delete("/runners/{runner_id}", status_code=204)
async def revoke_runner(
    runner_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    row = await repo.get_runner_row(db, runner_id)
    if row is None or row["profile_id"] != profile_id:
        raise HTTPException(status_code=404, detail="Runner not found")
    await repo.revoke_runner(db, runner_id)
    await audit_repository.record(
        db, user.id, "graph_workflow.runner.revoke", resource=runner_id, ip=_client_ip(request)
    )


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


# ── test suites (roadmap fase 11.1) ─────────────────────────────────────────

@router.get("/{wf_id}/test-cases", response_model=list[WorkflowTestCaseOut])
async def list_test_cases(
    wf_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await _owned(db, wf_id, profile_id)
    return await repo.list_test_cases(db, wf_id)


@router.post("/{wf_id}/test-cases", response_model=WorkflowTestCaseOut, status_code=201)
async def create_test_case(
    wf_id: str,
    body: WorkflowTestCaseIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    await _owned(db, wf_id, profile_id)
    case_id = await repo.create_test_case(
        db, wf_id, body.name, body.trigger_payload,
        [a.model_dump() for a in body.assertions],
    )
    await audit_repository.record(
        db, user.id, "graph_workflow.test_case.create", resource=wf_id, detail=case_id, ip=_client_ip(request)
    )
    return await repo.get_test_case(db, case_id)


@router.put("/{wf_id}/test-cases/{case_id}", response_model=WorkflowTestCaseOut)
async def update_test_case(
    wf_id: str,
    case_id: str,
    body: WorkflowTestCaseIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    await _owned(db, wf_id, profile_id)
    existing = await repo.get_test_case(db, case_id)
    if existing is None or existing.workflow_id != wf_id:
        raise HTTPException(status_code=404, detail="Test case not found")
    await repo.update_test_case(
        db, case_id, body.name, body.trigger_payload,
        [a.model_dump() for a in body.assertions],
    )
    await audit_repository.record(
        db, user.id, "graph_workflow.test_case.update", resource=wf_id, detail=case_id, ip=_client_ip(request)
    )
    return await repo.get_test_case(db, case_id)


@router.delete("/{wf_id}/test-cases/{case_id}", status_code=204)
async def delete_test_case(
    wf_id: str,
    case_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    await _owned(db, wf_id, profile_id)
    existing = await repo.get_test_case(db, case_id)
    if existing is None or existing.workflow_id != wf_id:
        raise HTTPException(status_code=404, detail="Test case not found")
    await repo.delete_test_case(db, case_id)
    await audit_repository.record(
        db, user.id, "graph_workflow.test_case.delete", resource=wf_id, detail=case_id, ip=_client_ip(request)
    )


@router.post("/{wf_id}/test-cases/run", response_model=TestSuiteRunOut)
async def run_test_suite(
    wf_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Fase 11.1 — run every saved test case: each executes the workflow with
    its fixture $trigger (pinned nodes replace external calls) and checks its
    assertions against the actual node outputs."""
    await _owned(db, wf_id, profile_id)
    result = await engine.run_test_suite(db, wf_id, profile_id)
    await audit_repository.record(
        db, user.id, "graph_workflow.test_suite.run", resource=wf_id,
        detail=f"{result.passed}/{result.total} passed", ip=_client_ip(request),
    )
    return result


# ── dry-run (roadmap fase 11.2) ──────────────────────────────────────────────

@router.post("/{wf_id}/dry-run", response_model=WorkflowDryRunOut)
async def dry_run(
    wf_id: str,
    body: WorkflowDryRunIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Fase 11.2 — simulate the whole graph: http.request/db.query/
    notification.*/llm.* are mocked (pinned output when present, else a typed
    placeholder) so nothing external actually happens. Use before activating
    a schedule on a new graph."""
    await _owned(db, wf_id, profile_id)
    try:
        result = await engine.dry_run_workflow(db, wf_id, profile_id, body.payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_repository.record(
        db, user.id, "graph_workflow.dry_run", resource=wf_id, ip=_client_ip(request)
    )
    return result


# ── cost estimate (roadmap fase 11.3) ────────────────────────────────────────

@router.get("/{wf_id}/cost-estimate", response_model=WorkflowCostEstimateOut)
async def cost_estimate(
    wf_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Fase 11.3 — static tokens/month projection from historical LLM usage
    (fase 5.1/7.4) and the workflow's active schedule frequency."""
    await _owned(db, wf_id, profile_id)
    try:
        return await engine.cost_estimate(db, wf_id, profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{wf_id}/budget", response_model=WorkflowBudgetStatusOut)
async def workflow_budget(
    wf_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Fase 12.1 — this workflow's own budget caps and usage for the current
    period, plus the profile-wide ("workspace") cap it is also gated by."""
    await _owned(db, wf_id, profile_id)
    try:
        return await engine.budget_status(db, wf_id, profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
            environment=body.environment,
            debug=body.debug, breakpoints=body.breakpoints,
            priority=body.priority,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_repository.record(db, user.id, "graph_workflow.run", resource=wf_id, ip=_client_ip(request))
    return {"run_id": run_id}


@router.post("/{wf_id}/chat", response_model=WorkflowChatOut)
async def chat_turn(
    wf_id: str,
    body: WorkflowChatIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),  # noqa: ARG001 — enforce auth
):
    """Fase 9.3 — one conversation turn against a ``chat``-triggered workflow.
    Runs the workflow synchronously with $trigger = {session_id, message,
    history}; the terminal ``chat.reply`` node's text comes back as ``reply``
    and the turn is appended to the session history (persisted, TTL-purged)."""
    wf = await _owned(db, wf_id, profile_id)
    triggers = await repo.list_triggers(db, wf_id)
    if not any(t.type == "chat" for t in triggers):
        raise HTTPException(status_code=400, detail="This workflow has no 'chat' trigger.")

    session_id = (body.session_id or secrets.token_urlsafe(12))[:128]
    history = await repo.get_chat_history(db, wf_id, session_id)
    trigger_payload = {"session_id": session_id, "message": body.message, "history": history}
    try:
        result = await engine.run_workflow_sync(
            db, wf_id, profile_id, trigger_type="chat", trigger_payload=trigger_payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get("status") != "completed":
        raise HTTPException(
            status_code=502,
            detail=f"Chat workflow did not complete ({result.get('status')}): {result.get('error') or 'unknown error'}",
        )
    reply = result.get("reply")
    if reply is None:
        reply = ""  # graph without a chat.reply node — still records the turn
    max_turns = max(1, int(settings.graph_workflow_chat_history_max_turns))
    history = (history + [
        {"role": "user", "content": body.message},
        {"role": "assistant", "content": reply},
    ])[-2 * max_turns:]
    await repo.upsert_chat_history(db, wf_id, profile_id, session_id, history)
    _ = wf  # ownership already enforced
    return WorkflowChatOut(session_id=session_id, reply=reply, run_id=result["run_id"])


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
    elif body.type in ("file.watch", "email.inbound", "rss.read"):
        # Fase 6.2 / 15.4 — poll-based triggers: next_run_at doubles as the
        # next-poll timestamp; leaving it NULL makes the first poll happen right away.
        if body.type == "email.inbound" and not str(config.get("host") or "").strip():
            raise HTTPException(status_code=400, detail="email.inbound trigger needs a 'host' (IMAP server)")
        if body.type == "rss.read" and not str(config.get("url") or "").strip().startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="rss.read trigger needs a 'url' (RSS/Atom feed http(s) URL)")
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
        # Fase 6.1 — a schedule may carry MULTIPLE cron expressions ('crons'
        # list, or 'cron' as a list) for mixed timetables; a single 'cron'
        # string keeps the original behaviour and encoding.
        raw = config.get("crons") if config.get("crons") not in (None, "", []) else config.get("cron")
        exprs = [str(e).strip() for e in (raw if isinstance(raw, list) else [raw]) if str(e or "").strip()]
        if not exprs:
            raise HTTPException(status_code=400, detail="'cron' (or 'crons') is required")
        firsts: list[datetime] = []
        for expr in exprs:
            fields = expr.split()
            if len(fields) != 5:
                raise HTTPException(status_code=400, detail=f"'{expr}': cron must have 5 space-separated fields")
            try:
                firsts.append(croniter(expr, now).get_next(datetime))
            except (ValueError, KeyError) as exc:
                raise HTTPException(status_code=400, detail=f"invalid cron expression '{expr}': {exc}") from None
        if len(exprs) == 1:
            recurrence = "cron:" + ",".join(exprs[0].split())
        else:
            recurrence = "crons:" + "|".join(",".join(e.split()) for e in exprs)
        return int(min(firsts).timestamp()), {**config, "recurrence": recurrence}

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
    try:
        # Fase 16.2 — idempotency: a `dedupKey` on the trigger dedupes repeated
        # deliveries within its window (returns the original run). Fase 7.2 —
        # environment; fase 16.4 — priority; both read from the trigger config.
        run_id, deduped = await engine.run_from_trigger(db, trigger, wf, "webhook", payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run_id": run_id, "deduped": deduped}


# ── public remote-runner protocol (Phase 46 — roadmap fase 14.1) ───────────
# Outbound-only from the runner's point of view: it calls these, the backend
# never calls it. Authenticated by X-Runner-Token (a per-runner secret, never
# the user's session) instead of the usual bearer/profile dependencies.

async def _runner_auth(
    x_runner_token: str | None, db: aiosqlite.Connection,
) -> aiosqlite.Row:
    if not x_runner_token:
        raise HTTPException(status_code=401, detail="Missing X-Runner-Token")
    runner = await repo.get_runner_by_token(db, x_runner_token)
    if runner is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked runner token")
    return runner


@public_router.post("/runners/heartbeat")
async def runner_heartbeat(
    body: RunnerHeartbeatIn,
    x_runner_token: str | None = Header(default=None),
    db: aiosqlite.Connection = Depends(get_db),
):
    runner = await _runner_auth(x_runner_token, db)
    await repo.heartbeat_runner(db, runner["id"], version=body.version, labels=body.labels)
    return {"ok": True}


@public_router.get("/runners/jobs/next")
async def poll_runner_job(
    wait: float = 20.0,
    x_runner_token: str | None = Header(default=None),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Long-poll for the next job assigned to this runner: claims and returns
    it (the fase 3.1 test_node() request shape) as soon as one is queued, else
    204 once ``wait`` seconds (capped) pass with nothing to do."""
    runner = await _runner_auth(x_runner_token, db)
    deadline = time.time() + min(max(wait, 0.0), 55.0)
    while True:
        row = await repo.claim_next_runner_job(db, runner["id"])
        if row is not None:
            payload = json.loads(row["payload_json"])
            return RunnerJobOut(
                job_id=row["id"], node_id=row["node_id"], node_type=row["node_type"],
                params=payload.get("params") or {}, input=payload.get("input"),
            )
        if time.time() >= deadline:
            return Response(status_code=204)
        await asyncio.sleep(0.5)


@public_router.post("/runners/jobs/{job_id}/result")
async def submit_runner_job_result(
    job_id: str,
    body: RunnerJobResultIn,
    x_runner_token: str | None = Header(default=None),
    db: aiosqlite.Connection = Depends(get_db),
):
    runner = await _runner_auth(x_runner_token, db)
    row = await repo.get_runner_job(db, job_id)
    if row is None or row["runner_id"] != runner["id"]:
        raise HTTPException(status_code=404, detail="Job not found")
    settled = await repo.finish_runner_job(
        db, job_id, ok=body.ok,
        result={"output": body.output, "handles": body.handles} if body.ok else None,
        error=body.error,
    )
    if not settled:
        raise HTTPException(status_code=409, detail="Job already finished")
    return {"ok": True}
