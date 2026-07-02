"""
Phase 18 — user-defined custom tools, sandboxed code interpreter,
persistent multi-step workflows.
"""

import asyncio

import httpx
import pytest


# ── Custom tools ──────────────────────────────────────────────────────────────
def _tool_body(name="echo_tool", url="https://example.invalid/echo", enabled=True):
    return {
        "name": name,
        "description": "Echoes the payload back",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "endpoint": {"url": url, "method": "POST"},
        "enabled": enabled,
    }


def test_custom_tool_crud(client, auth_headers):
    # create
    resp = client.post("/api/v1/tools/custom", json=_tool_body(), headers=auth_headers)
    assert resp.status_code == 201, resp.text
    tool = resp.json()
    assert tool["name"] == "echo_tool"
    assert tool["enabled"] is True

    # appears in the profile tool list, namespaced
    resp = client.get("/api/v1/tools", headers=auth_headers)
    assert resp.status_code == 200
    names = [t["function"]["name"] for t in resp.json()]
    assert "custom__echo_tool" in names

    # upsert by name updates instead of duplicating
    resp = client.post(
        "/api/v1/tools/custom",
        json=_tool_body(url="https://example.invalid/echo2"),
        headers=auth_headers,
    )
    assert resp.status_code == 201
    resp = client.get("/api/v1/tools/custom", headers=auth_headers)
    tools = [t for t in resp.json() if t["name"] == "echo_tool"]
    assert len(tools) == 1
    assert tools[0]["endpoint"]["url"] == "https://example.invalid/echo2"

    # disable → gone from the chat tool list
    resp = client.patch(
        f"/api/v1/tools/custom/{tool['id']}", json={"enabled": False}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    names = [t["function"]["name"] for t in client.get("/api/v1/tools", headers=auth_headers).json()]
    assert "custom__echo_tool" not in names

    # delete
    resp = client.delete(f"/api/v1/tools/custom/{tool['id']}", headers=auth_headers)
    assert resp.status_code == 204
    assert client.get("/api/v1/tools/custom", headers=auth_headers).json() == []


def test_custom_tool_validation(client, auth_headers):
    bad = _tool_body(name="bad name!")
    assert client.post("/api/v1/tools/custom", json=bad, headers=auth_headers).status_code == 422
    bad = _tool_body()
    bad["endpoint"]["url"] = "ftp://nope"
    assert client.post("/api/v1/tools/custom", json=bad, headers=auth_headers).status_code == 422


def test_custom_tool_invoke(monkeypatch):
    """invoke() sends arguments as JSON body and returns the response text."""
    from app.schemas.custom_tools import CustomToolEndpoint, CustomToolOut
    from app.services import custom_tool_service

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, text='{"ok": true}')

    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)

    tool = CustomToolOut(
        id="t1", profile_id="default", name="echo_tool",
        parameters={"type": "object", "properties": {}},
        endpoint=CustomToolEndpoint(
            url="https://example.invalid/echo",
            method="POST",
            auth={"type": "bearer", "token": "sekret"},
        ),
        enabled=True, created_at=0, updated_at=0,
    )
    result = asyncio.run(custom_tool_service.invoke(tool, {"text": "hi"}))
    assert result == '{"ok": true}'
    assert seen["body"] in ('{"text": "hi"}', '{"text":"hi"}')
    assert seen["auth"] == "Bearer sekret"


# ── Code interpreter ──────────────────────────────────────────────────────────
def test_python_exec_stdout():
    from app.tools.code_interpreter import python_exec

    result = asyncio.run(python_exec("print(6 * 7)"))
    assert "42" in result


def test_python_exec_input_and_output_files():
    from app.tools.code_interpreter import python_exec

    code = (
        "data = open('in.txt').read()\n"
        "open('out.txt', 'w').write(data.upper())\n"
        "print('done')\n"
    )
    result = asyncio.run(python_exec(code, files={"in.txt": "ciao"}))
    assert "done" in result
    assert "file created: out.txt" in result
    assert "CIAO" in result


def test_python_exec_network_blocked():
    from app.tools.code_interpreter import python_exec

    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('example.com', 80), timeout=2)\n"
        "    print('CONNECTED')\n"
        "except OSError as exc:\n"
        "    print(f'BLOCKED: {exc}')\n"
    )
    result = asyncio.run(python_exec(code))
    assert "BLOCKED" in result
    assert "CONNECTED" not in result


def test_python_exec_error_reported():
    from app.tools.code_interpreter import python_exec

    result = asyncio.run(python_exec("raise ValueError('boom')"))
    assert "ValueError" in result and "boom" in result
    assert "exit code" in result


def test_python_exec_rejects_path_traversal():
    from app.tools.code_interpreter import python_exec

    result = asyncio.run(python_exec("print(1)", files={"../evil.txt": "x"}))
    assert "invalid input file name" in result


def test_python_exec_listed_in_tools(client, auth_headers):
    names = [t["function"]["name"] for t in client.get("/api/v1/tools", headers=auth_headers).json()]
    assert "python_exec" in names


# ── Persistent workflows ──────────────────────────────────────────────────────
# NB: TestClient tears down its event loop after every request, so a
# background loop started inside a request would be killed before it runs.
# The tests no-op the auto-start and drive the loop synchronously instead.
@pytest.fixture()
def no_autostart(monkeypatch):
    from app.services import workflow_service

    monkeypatch.setattr(workflow_service, "start", lambda *a, **k: None)


def test_workflow_lifecycle(client, auth_headers, no_autostart):
    from app.services import workflow_service

    resp = client.post(
        "/api/v1/workflows",
        json={"goal": "Say hello", "model": "mock/spice-sibyl-1", "max_steps": 3},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    run = resp.json()
    assert run["status"] == "pending"
    assert run["max_steps"] == 3

    # The mock provider answers without tool calls → completes at iteration 1.
    asyncio.run(workflow_service._run_loop(run["id"]))

    run = client.get(f"/api/v1/workflows/{run['id']}", headers=auth_headers).json()
    assert run["status"] == "completed"
    assert run["result"]
    kinds = [s["kind"] for s in run["steps"]]
    assert "final" in kinds

    # list contains it
    runs = client.get("/api/v1/workflows", headers=auth_headers).json()
    assert any(r["id"] == run["id"] for r in runs)

    # pause on a finished run → 409
    resp = client.post(f"/api/v1/workflows/{run['id']}/pause", headers=auth_headers)
    assert resp.status_code == 409

    # delete
    resp = client.delete(f"/api/v1/workflows/{run['id']}", headers=auth_headers)
    assert resp.status_code == 204
    resp = client.get(f"/api/v1/workflows/{run['id']}", headers=auth_headers)
    assert resp.status_code == 404


def test_workflow_pause_resume_checkpoint(client, auth_headers, no_autostart):
    """A run interrupted mid-flight is reconciled to paused and can resume
    from its checkpoint to completion."""
    import aiosqlite

    from app.core.config import settings
    from app.db import workflow_repository
    from app.services import workflow_service

    resp = client.post(
        "/api/v1/workflows",
        json={"goal": "Do the thing", "model": "mock/spice-sibyl-1"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    run_id = resp.json()["id"]

    # Simulate a restart mid-run: status 'running' but no live task.
    async def _mark_running():
        db = await aiosqlite.connect(settings.db_path)
        db.row_factory = aiosqlite.Row
        try:
            await workflow_repository.set_status(db, run_id, "running")
        finally:
            await db.close()

    asyncio.run(_mark_running())

    # GET reconciles it to paused (resumable).
    run = client.get(f"/api/v1/workflows/{run_id}", headers=auth_headers).json()
    assert run["status"] == "paused"

    # resume flips it back to running (start is no-op'd) …
    resp = client.post(f"/api/v1/workflows/{run_id}/resume", headers=auth_headers)
    assert resp.status_code == 200

    # … and the loop picks it up from the checkpoint and completes.
    asyncio.run(workflow_service._run_loop(run_id))
    run = client.get(f"/api/v1/workflows/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed"

    client.delete(f"/api/v1/workflows/{run_id}", headers=auth_headers)


def test_workflow_max_steps_capped(client, auth_headers, no_autostart):
    from app.core.config import settings

    resp = client.post(
        "/api/v1/workflows",
        json={"goal": "x", "model": "mock/spice-sibyl-1", "max_steps": 10_000},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    run = resp.json()
    assert run["max_steps"] == settings.workflow_max_steps_limit
    client.delete(f"/api/v1/workflows/{run['id']}", headers=auth_headers)
