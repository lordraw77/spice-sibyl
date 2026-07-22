"""
Phase 41 — roadmap fase 9: workflows as ecosystem tools.

Covers:
  9.1 — an active workflow with an input contract and `expose_as_tool` is
        published as a `workflow__<id>` tool (llm.agent / registry), invocation
        runs the workflow and returns its output, with an anti-recursion cap;
  9.2 — the product MCP server (`POST /mcp`) publishes those workflows over
        JSON-RPC: initialize / tools/list / tools/call;
  9.3 — the `chat` trigger + `chat.reply` node: POST /{id}/chat runs the
        workflow with {session_id, message, history}, persists the session and
        returns the reply;
  9.4 — OpenAPI import turns a spec into preconfigured http.request node drafts.
"""

import asyncio

import pytest

from app.services import workflow_graph_service as engine
from app.services import workflow_tool_service


@pytest.fixture(autouse=True)
def _reset_sse_appstatus():
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None


def _make_wf(client, auth_headers, name, graph, **extra):
    resp = client.post(
        "/api/v1/graph-workflows", json={"name": name, "graph": graph, **extra},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_trigger(client, auth_headers, wf_id, ttype, config=None):
    resp = client.post(
        f"/api/v1/graph-workflows/{wf_id}/triggers",
        json={"type": ttype, "config": config or {}}, headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _activate(client, auth_headers, wf_id):
    resp = client.post(f"/api/v1/graph-workflows/{wf_id}/activate", headers=auth_headers)
    assert resp.status_code == 200, resp.text


# A tiny contract-carrying workflow: manual → set (echoes the trigger name).
_ECHO_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "s", "type": "set", "params": {"fields": {"greeting": "Hello {{ $trigger.name }}"}}},
    ],
    "edges": [{"id": "e1", "source": "t", "target": "s"}],
}
_ECHO_CONTRACT = {
    "type": "object",
    "properties": {"name": {"type": "string", "description": "who to greet"}},
    "required": ["name"],
}


# ── fase 9.1: workflow exposed as a tool ─────────────────────────────────────

def test_expose_as_tool_flag_roundtrips(client, auth_headers):
    wf = _make_wf(client, auth_headers, "echo", _ECHO_GRAPH,
                  input_schema=_ECHO_CONTRACT, expose_as_tool=True)
    assert wf["expose_as_tool"] is True
    got = client.get(f"/api/v1/graph-workflows/{wf['id']}", headers=auth_headers).json()
    assert got["expose_as_tool"] is True


def test_tools_listing_only_includes_active_exposed_contract_workflows(client, auth_headers):
    # Not active yet → not listed.
    wf = _make_wf(client, auth_headers, "echo tool", _ECHO_GRAPH,
                  input_schema=_ECHO_CONTRACT, expose_as_tool=True)
    tools = client.get("/api/v1/graph-workflows/tools", headers=auth_headers).json()
    assert all(t["workflow_id"] != wf["id"] for t in tools)

    _activate(client, auth_headers, wf["id"])
    tools = client.get("/api/v1/graph-workflows/tools", headers=auth_headers).json()
    mine = [t for t in tools if t["workflow_id"] == wf["id"]]
    assert len(mine) == 1
    assert mine[0]["tool_name"] == f"workflow__{wf['id']}"
    assert mine[0]["parameters"]["properties"]["name"]["type"] == "string"


def test_workflow_tool_invocation_runs_the_workflow(client, auth_headers):
    wf = _make_wf(client, auth_headers, "echo call", _ECHO_GRAPH,
                  input_schema=_ECHO_CONTRACT, expose_as_tool=True)
    _activate(client, auth_headers, wf["id"])

    async def _call():
        from app.tools.registry import execute_tool

        return await execute_tool(
            workflow_tool_service.namespaced(wf["id"]), {"name": "Ada"}, profile_id=wf["profile_id"]
        )

    result = asyncio.run(_call())
    assert "Hello Ada" in result


def test_workflow_tool_refuses_when_not_exposed(client, auth_headers):
    wf = _make_wf(client, auth_headers, "not exposed", _ECHO_GRAPH,
                  input_schema=_ECHO_CONTRACT, expose_as_tool=False)
    _activate(client, auth_headers, wf["id"])

    async def _call():
        return await workflow_tool_service.call_tool(
            workflow_tool_service.namespaced(wf["id"]), {"name": "x"}, wf["profile_id"]
        )

    result = asyncio.run(_call())
    assert "not currently exposed" in result


def test_workflow_tool_depth_guard(client, auth_headers, monkeypatch):
    wf = _make_wf(client, auth_headers, "deep", _ECHO_GRAPH,
                  input_schema=_ECHO_CONTRACT, expose_as_tool=True)
    _activate(client, auth_headers, wf["id"])
    monkeypatch.setattr(engine.settings, "graph_workflow_tool_max_depth", 1)
    # Simulate being one level deep already.
    token = workflow_tool_service._call_depth.set(1)
    try:
        result = asyncio.run(
            workflow_tool_service.call_tool(
                workflow_tool_service.namespaced(wf["id"]), {"name": "x"}, "default"
            )
        )
    finally:
        workflow_tool_service._call_depth.reset(token)
    assert "recursion" in result.lower()


# ── fase 9.2: MCP server ─────────────────────────────────────────────────────

def _rpc(client, auth_headers, method, params=None, req_id=1):
    return client.post(
        "/api/v1/graph-workflows/mcp",
        json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}},
        headers=auth_headers,
    )


def test_mcp_initialize_and_tools_list_and_call(client, auth_headers):
    wf = _make_wf(client, auth_headers, "mcp echo", _ECHO_GRAPH,
                  input_schema=_ECHO_CONTRACT, expose_as_tool=True)
    _activate(client, auth_headers, wf["id"])

    init = _rpc(client, auth_headers, "initialize").json()
    assert init["result"]["serverInfo"]["name"] == "spice-sibyl-workflows"
    assert "tools" in init["result"]["capabilities"]

    listed = _rpc(client, auth_headers, "tools/list").json()
    names = [t["name"] for t in listed["result"]["tools"]]
    assert f"workflow__{wf['id']}" in names

    called = _rpc(
        client, auth_headers, "tools/call",
        {"name": f"workflow__{wf['id']}", "arguments": {"name": "Bob"}},
    ).json()
    assert called["result"]["isError"] is False
    assert "Hello Bob" in called["result"]["content"][0]["text"]


def test_mcp_unknown_method_returns_jsonrpc_error(client, auth_headers):
    resp = _rpc(client, auth_headers, "does/not/exist").json()
    assert resp["error"]["code"] == -32601


def test_mcp_notification_gets_202(client, auth_headers):
    resp = client.post(
        "/api/v1/graph-workflows/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=auth_headers,
    )
    assert resp.status_code == 202


# ── fase 9.3: chat trigger + chat.reply ──────────────────────────────────────

_CHAT_GRAPH = {
    "nodes": [
        {"id": "t", "type": "chat"},
        {"id": "r", "type": "chat.reply",
         "params": {"text": "You said: {{ $trigger.message }}; seen: {{ $trigger.history }}"}},
    ],
    "edges": [{"id": "e1", "source": "t", "target": "r"}],
}


def test_chat_requires_chat_trigger(client, auth_headers):
    wf = _make_wf(client, auth_headers, "no chat trigger", _CHAT_GRAPH)
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/chat", json={"message": "hi"}, headers=auth_headers
    )
    assert resp.status_code == 400


def test_chat_turn_replies_and_persists_session(client, auth_headers):
    wf = _make_wf(client, auth_headers, "chatbot", _CHAT_GRAPH)
    _add_trigger(client, auth_headers, wf["id"], "chat")

    first = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/chat", json={"message": "hello"}, headers=auth_headers
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert "You said: hello" in body["reply"]
    session_id = body["session_id"]
    assert session_id

    # Second turn on the same session sees the growing history.
    second = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/chat",
        json={"message": "again", "session_id": session_id}, headers=auth_headers,
    ).json()
    assert "You said: again" in second["reply"]
    # the second turn sees the first turn's messages in its history.
    assert "hello" in second["reply"]


# ── fase 9.4: OpenAPI import ─────────────────────────────────────────────────

_OPENAPI_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Demo API"},
    "servers": [{"url": "https://api.demo.test/v1"}],
    "components": {
        "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}
    },
    "paths": {
        "/users": {
            "get": {
                "operationId": "listUsers",
                "summary": "List users",
                "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
            },
            "post": {"operationId": "createUser", "requestBody": {"content": {}}},
        },
        "/health": {"get": {"operationId": "health"}},
    },
}


def test_openapi_import_generates_http_request_nodes(client, auth_headers):
    resp = client.post(
        "/api/v1/graph-workflows/openapi/import",
        json={"spec": _OPENAPI_SPEC}, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["api_title"] == "Demo API"
    assert body["base_url"] == "https://api.demo.test/v1"
    ops = {o["operation_id"]: o for o in body["operations"]}
    assert set(ops) == {"listUsers", "createUser", "health"}

    list_users = ops["listUsers"]["node"]
    assert list_users["type"] == "http.request"
    assert list_users["params"]["method"] == "GET"
    assert list_users["params"]["url"] == "https://api.demo.test/v1/users"
    assert "limit" in list_users["params"]["query"]
    # bearer auth → Authorization header placeholder referencing $secrets
    assert "$secrets.API_TOKEN" in list_users["params"]["headers"]["Authorization"]

    create_user = ops["createUser"]["node"]
    assert create_user["params"]["method"] == "POST"
    assert "body" in create_user["params"]


def test_openapi_import_path_prefix_filter(client, auth_headers):
    resp = client.post(
        "/api/v1/graph-workflows/openapi/import",
        json={"spec": _OPENAPI_SPEC, "path_prefix": "/health"}, headers=auth_headers,
    ).json()
    assert [o["operation_id"] for o in resp["operations"]] == ["health"]


def test_openapi_import_rejects_non_spec(client, auth_headers):
    resp = client.post(
        "/api/v1/graph-workflows/openapi/import",
        json={"spec": {"not": "a spec"}}, headers=auth_headers,
    )
    assert resp.status_code == 422
