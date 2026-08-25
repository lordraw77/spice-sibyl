"""Import/export ecosystem: snapshot import, exposed tools, OpenAPI import,
the produced MCP server and LLM draft generation.

Extracted verbatim from the former single-file graph_workflows.py.
"""

import asyncio
import json

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sse_starlette.sse import EventSourceResponse

from app.db import audit_repository, graph_workflow_repository as repo
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.schemas.auth import UserOut
from app.schemas.graph_workflows import (
    ExposedWorkflowToolOut,
    OpenApiImportIn,
    OpenApiImportOut,
    WorkflowGenerateIn,
    WorkflowGenerateOut,
    WorkflowImportIn,
    WorkflowImportOut,
)
from app.services import workflow_graph_service as engine

from ._common import _client_ip

router = APIRouter()

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
