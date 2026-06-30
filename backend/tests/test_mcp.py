"""Phase 18 — MCP server registry + stdio client tests."""

import asyncio
import sys
from pathlib import Path

import pytest

from app.schemas.mcp import McpConfigBundle, McpServerConfig, McpServerOut
from app.services import mcp_client, mcp_service

_FAKE = str(Path(__file__).parent / "_fake_mcp_server.py")


def _fake_config(**overrides) -> McpServerConfig:
    cfg = {"command": sys.executable, "args": [_FAKE]}
    cfg.update(overrides)
    return McpServerConfig(**cfg)


# ── stdio client (end-to-end against the fake server) ─────────────────────────
def test_client_lists_and_calls_tools():
    async def run():
        async with mcp_client.open_session(_fake_config()) as session:
            tools = await session.list_tools()
            assert [t.name for t in tools] == ["echo"]
            assert tools[0].input_schema["required"] == ["text"]
            result = await session.call_tool("echo", {"text": "hi"})
            assert result == "echo: hi"

    asyncio.run(run())


def test_client_bad_command_raises():
    async def run():
        with pytest.raises(mcp_client.MCPError):
            async with mcp_client.open_session(
                McpServerConfig(command="/nonexistent/cmd-xyz", args=[])
            ):
                pass

    asyncio.run(run())


def test_sse_config_accepted_and_inferred():
    cfg = McpServerConfig(type="sse", url="http://host:9999/mcp/sse")
    assert cfg.transport == "sse"
    # transport inferred from url when type omitted
    assert McpServerConfig(url="http://host/sse").transport == "sse"
    # sse without url is rejected
    with pytest.raises(Exception):
        McpServerConfig(type="sse")


def test_sse_session_event_handling():
    from app.services.mcp_client import _SseSession

    async def run():
        s = _SseSession(client=None, stream_response=None, base_url="http://h:9999/mcp/sse")
        # 'endpoint' event resolves a relative path against the base origin
        s._handle_event("endpoint", "/mcp/messages?sid=1")
        assert s._endpoint == "http://h:9999/mcp/messages?sid=1"
        assert s._endpoint_ready.is_set()

        # a result message resolves the matching pending future
        fut = asyncio.get_event_loop().create_future()
        s._pending[7] = fut
        s._handle_event("message", '{"jsonrpc":"2.0","id":7,"result":{"ok":true}}')
        assert await asyncio.wait_for(fut, 1) == {"ok": True}

        # an error message fails the future with MCPError
        fut2 = asyncio.get_event_loop().create_future()
        s._pending[8] = fut2
        s._handle_event("message", '{"jsonrpc":"2.0","id":8,"error":{"code":-1,"message":"boom"}}')
        with pytest.raises(mcp_client.MCPError, match="boom"):
            await asyncio.wait_for(fut2, 1)

    asyncio.run(run())


# ── manager service (namespacing + routing) ───────────────────────────────────
def test_namespacing_and_is_mcp_tool():
    name = mcp_service.namespaced("wiki llm", "search.docs")
    assert name == "mcp__wiki_llm__search_docs"
    assert mcp_service.is_mcp_tool(name)
    assert not mcp_service.is_mcp_tool("calculator")


def test_service_refresh_builds_tool_defs_and_routes(client, auth_headers):
    """Register a fake server, refresh, and confirm its tool is discoverable +
    callable through the manager's routing cache."""
    from app.db.database import get_db

    async def run():
        agen = get_db()
        db = await agen.__anext__()
        try:
            from app.db import mcp_repository

            await mcp_repository.upsert_server(db, "faker", _fake_config(), True)
            servers = await mcp_service.refresh(db)
            faker = next(s for s in servers if s.name == "faker")
            assert faker.status == "ok"
            assert any(t.name == "echo" for t in faker.tools)

            defs = mcp_service.get_tool_definitions()
            names = [d["function"]["name"] for d in defs]
            assert "mcp__faker__echo" in names

            out = await mcp_service.call_tool("mcp__faker__echo", {"text": "yo"})
            assert out == "echo: yo"
        finally:
            await mcp_repository.delete_server(db, faker.id)
            await agen.aclose()

    asyncio.run(run())


def test_call_unknown_mcp_tool_is_graceful():
    out = asyncio.run(mcp_service.call_tool("mcp__nope__missing", {}))
    assert "Unknown or unavailable" in out


# ── HTTP endpoints (admin CRUD + import/export) ───────────────────────────────
def test_crud_and_toggle(client, auth_headers):
    payload = {
        "name": "wikillm",
        "config": {"command": "docker", "args": ["run", "--rm", "-i", "img", "python", "run_stdio.py"]},
        "enabled": False,  # disabled → not probed, so create won't try to launch docker
    }
    resp = client.post("/api/v1/mcp/servers", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    server = resp.json()
    sid = server["id"]
    assert server["name"] == "wikillm"
    assert server["status"] == "disabled"
    assert server["config"]["args"][0] == "run"

    # List
    resp = client.get("/api/v1/mcp/servers", headers=auth_headers)
    assert any(s["id"] == sid for s in resp.json())

    # Export round-trips the standard bundle shape
    resp = client.get("/api/v1/mcp/config", headers=auth_headers)
    bundle = resp.json()
    assert "wikillm" in bundle["mcpServers"]
    assert bundle["mcpServers"]["wikillm"]["command"] == "docker"

    # Toggle enabled (still won't launch on a GET/PATCH probe failure → status error is fine)
    resp = client.patch(f"/api/v1/mcp/servers/{sid}", json={"enabled": True}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True

    # Delete
    resp = client.delete(f"/api/v1/mcp/servers/{sid}", headers=auth_headers)
    assert resp.status_code == 204
    resp = client.get(f"/api/v1/mcp/servers/{sid}", headers=auth_headers)
    assert resp.status_code == 404


def test_import_bundle(client, auth_headers):
    bundle = {"mcpServers": {
        "alpha": {"command": "echo", "args": ["hi"]},
        "beta": {"command": "echo", "args": ["yo"]},
    }}
    resp = client.post("/api/v1/mcp/import?enabled=false", json=bundle, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    names = {s["name"] for s in resp.json()}
    assert {"alpha", "beta"} <= names
    # cleanup
    for s in client.get("/api/v1/mcp/servers", headers=auth_headers).json():
        if s["name"] in ("alpha", "beta"):
            client.delete(f"/api/v1/mcp/servers/{s['id']}", headers=auth_headers)


def test_mcp_requires_auth(client):
    resp = client.get("/api/v1/mcp/servers")
    assert resp.status_code == 401


def test_tools_endpoint_includes_builtins(client, auth_headers):
    resp = client.get("/api/v1/tools", headers=auth_headers)
    assert resp.status_code == 200
    names = [t["function"]["name"] for t in resp.json()]
    assert "calculator" in names
