"""
Phase 41 — the product's workflow MCP server (roadmap fase 9.2).

Extends fase 9.1 outwards: workflows the profile publishes as tools
(``expose_as_tool`` + active + input contract) are also reachable by **external
MCP clients** (Claude Desktop, IDEs, …) over a minimal JSON-RPC 2.0 endpoint —
the "streamable HTTP" transport, single POST per request, no SSE needed for the
tool surface we expose.

Only the tool primitives are implemented (this server exposes tools, not
resources/prompts): ``initialize``, ``tools/list``, ``tools/call``, ``ping``,
and the ``notifications/*`` no-ops. Authentication is the caller's normal API
credential (the endpoint depends on the authenticated user); a ``tools/call``
runs the workflow inline (recorded with trigger origin ``mcp``) and returns its
output as MCP ``content``.
"""

import json
import logging

import aiosqlite

from app.services import workflow_tool_service as wf_tools
from app.services import workflow_graph_service as engine

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "spice-sibyl-workflows"
SERVER_VERSION = "1.0.0"

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _ok(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def _tools_list(db: aiosqlite.Connection, profile_id: str) -> dict:
    descriptors = await wf_tools.list_descriptors(db, profile_id)
    tools = [
        {
            "name": d.tool_name,
            "title": d.workflow_name,
            "description": d.description,
            "inputSchema": d.parameters or {"type": "object", "properties": {}},
        }
        for d in descriptors
    ]
    return {"tools": tools}


async def _tools_call(db: aiosqlite.Connection, profile_id: str, params: dict) -> dict:
    name = str(params.get("name") or "")
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    if not wf_tools.is_workflow_tool(name):
        return {
            "content": [{"type": "text", "text": f"Unknown tool '{name}'."}],
            "isError": True,
        }
    wf_id = wf_tools.raw_id(name)
    wf = await engine.repo.get_workflow(db, wf_id)
    if wf is None or wf.profile_id != profile_id or not (wf.expose_as_tool and wf.active and wf.input_schema):
        return {
            "content": [{"type": "text", "text": f"Tool '{name}' is not available."}],
            "isError": True,
        }
    try:
        result = await engine.run_workflow_sync(
            db, wf_id, profile_id,
            trigger_type="mcp", trigger_payload=arguments, depth=0,
        )
    except ValueError as exc:  # contract violation
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except Exception as exc:  # noqa: BLE001
        logger.exception("MCP tools/call failed for %s", wf_id)
        return {"content": [{"type": "text", "text": f"Workflow failed: {exc}"}], "isError": True}

    if result.get("status") != "completed":
        msg = f"Workflow did not complete ({result.get('status')}): {result.get('error') or 'unknown error'}"
        return {"content": [{"type": "text", "text": msg}], "isError": True}
    output = result.get("output")
    text = output if isinstance(output, str) else json.dumps(output, default=str, ensure_ascii=False)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": output if isinstance(output, dict) else {"result": output},
        "isError": False,
    }


async def handle_rpc(
    db: aiosqlite.Connection, profile_id: str, message: dict
) -> dict | None:
    """Dispatch one JSON-RPC message. Returns the response dict, or ``None`` for
    notifications (messages without an ``id``), which get no reply."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _err(message.get("id") if isinstance(message, dict) else None,
                    INVALID_REQUEST, "invalid JSON-RPC 2.0 request")
    method = message.get("method")
    req_id = message.get("id")
    is_notification = "id" not in message
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    try:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        elif method == "ping":
            result = {}
        elif method in ("notifications/initialized", "notifications/cancelled"):
            return None  # notifications: acknowledged, no response
        elif method == "tools/list":
            result = await _tools_list(db, profile_id)
        elif method == "tools/call":
            result = await _tools_call(db, profile_id, params)
        else:
            if is_notification:
                return None
            return _err(req_id, METHOD_NOT_FOUND, f"method not found: {method}")
    except Exception as exc:  # noqa: BLE001 — map any failure to a JSON-RPC error
        logger.exception("MCP handler error for method %s", method)
        if is_notification:
            return None
        return _err(req_id, INTERNAL_ERROR, str(exc))

    if is_notification:
        return None
    return _ok(req_id, result)


async def handle_message(
    db: aiosqlite.Connection, profile_id: str, body
) -> list | dict | None:
    """Handle a single message or a JSON-RPC batch (list). Returns the response
    payload the endpoint should serialise (``None`` = HTTP 202, nothing to
    return — e.g. a lone notification)."""
    if isinstance(body, list):
        if not body:
            return _err(None, INVALID_REQUEST, "empty batch")
        responses = []
        for msg in body:
            resp = await handle_rpc(db, profile_id, msg)
            if resp is not None:
                responses.append(resp)
        return responses or None
    return await handle_rpc(db, profile_id, body)
