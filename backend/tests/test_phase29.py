"""
Phase 29 — visual node-graph workflow engine.

Unit tests for the standalone expression resolver plus end-to-end API tests for
the DAG engine (CRUD, versioning, triggers, a deterministic run over tool/set/if
nodes driven by the mock provider).
"""

import asyncio

import pytest

from app.services import expression_resolver as er
from app.services import workflow_graph_service as engine


@pytest.fixture()
def captured_spawns(monkeypatch):
    """The TestClient runs each request on a short-lived loop that cancels
    fire-and-forget tasks, so intercept ``_spawn`` and let the test drive
    ``_execute`` itself (mirrors the Phase 18 ``no_autostart`` fixture)."""
    spawns: list[tuple] = []
    monkeypatch.setattr(engine, "_spawn", lambda *args: spawns.append(args))
    return spawns


def _drive_last_run(spawns):
    """Execute the most recently spawned run synchronously to completion."""
    asyncio.run(engine._execute(*spawns[-1]))


# ── expression resolver (standalone unit tests) ─────────────────────────────

def _ctx():
    return {
        "node": {"n1": {"output": {"title": "Hello", "items": [1, 2, 3]}}},
        "trigger": {"count": 5, "name": "world"},
        "env": {"API_KEY": "secret"},
        "json": {"value": 42},
        "now": 1000,
    }


def test_eval_path_navigation():
    assert er.eval_expression("$node.n1.output.title", _ctx()) == "Hello"
    assert er.eval_expression("$node.n1.output.items[1]", _ctx()) == 2
    assert er.eval_expression("$trigger.count", _ctx()) == 5
    assert er.eval_expression("$json.value", _ctx()) == 42
    assert er.eval_expression("$env.API_KEY", _ctx()) == "secret"


def test_eval_missing_keys_are_none():
    assert er.eval_expression("$node.nope.output.x", _ctx()) is None
    assert er.eval_expression("$trigger.missing", _ctx()) is None


def test_whitelisted_functions():
    ctx = _ctx()
    assert er.eval_expression("upper($node.n1.output.title)", ctx) == "HELLO"
    assert er.eval_expression("len($node.n1.output.items)", ctx) == 3
    assert er.eval_expression("join($node.n1.output.items, '-')", ctx) == "1-2-3"
    assert er.eval_expression("default($trigger.missing, 'fallback')", ctx) == "fallback"
    assert er.eval_expression("first($node.n1.output.items)", ctx) == 1


def test_operators_and_comparisons():
    ctx = _ctx()
    assert er.eval_expression("$trigger.count > 3", ctx) is True
    assert er.eval_expression("$trigger.count * 2", ctx) == 10
    assert er.eval_expression("$trigger.count == 5 and $json.value == 42", ctx) is True
    assert er.eval_expression("'a' if $trigger.count > 3 else 'b'", ctx) == "a"


def test_disallowed_names_and_calls_raise():
    with pytest.raises(er.ExpressionError):
        er.eval_expression("__import__('os')", _ctx())
    with pytest.raises(er.ExpressionError):
        er.eval_expression("open('/etc/passwd')", _ctx())
    with pytest.raises(er.ExpressionError):
        er.eval_expression("unknown_var", _ctx())


def test_interpolation_and_native_type():
    ctx = _ctx()
    # A single pure expression keeps its native type.
    assert asyncio.run(er.resolve_value("={{ $trigger.count }}", ctx)) == 5
    # Mixed text → string.
    assert asyncio.run(er.resolve_value("Hi ={{ $trigger.name }}!", ctx)) == "Hi world!"
    # Literals pass through untouched.
    assert asyncio.run(er.resolve_value("plain", ctx)) == "plain"
    assert asyncio.run(er.resolve_value(7, ctx)) == 7


def test_bare_interpolation_without_equals_resolves():
    # `{{ … }}` without the leading `=` is a common slip — it must resolve too.
    assert asyncio.run(er.resolve_value("Hi {{ $trigger.name }}!", _ctx())) == "Hi world!"
    # A single pure bare expression keeps its native type.
    assert asyncio.run(er.resolve_value("{{ $trigger.count }}", _ctx())) == 5
    # Canonical form still wins when both markers appear.
    assert asyncio.run(er.resolve_value("={{ $trigger.count > 3 }}", _ctx())) is True
    # Strings without a closing }} stay literal.
    assert asyncio.run(er.resolve_value("just {{ text", _ctx())) == "just {{ text"


def test_resolve_params_recurses():
    ctx = _ctx()
    params = {"a": "={{ $trigger.count }}", "b": {"c": "={{ upper($node.n1.output.title) }}"}}
    out = asyncio.run(er.resolve_params(params, ctx))
    assert out == {"a": 5, "b": {"c": "HELLO"}}


# ── engine API (end-to-end) ─────────────────────────────────────────────────

def test_node_types_catalog(client, auth_headers):
    resp = client.get("/api/v1/graph-workflows/node-types", headers=auth_headers)
    assert resp.status_code == 200
    catalog = resp.json()
    by_type = {t["type"]: t for t in catalog}
    assert {"manual", "if", "switch", "set", "code", "llm.agent"} <= set(by_type)
    # Every registry tool is exposed as a tool.<name> node.
    assert "tool.calculator" in by_type
    # AI nodes expose a `model` param rendered by the shared chat model picker.
    model_params = [p for p in by_type["llm.completion"]["params_schema"] if p["name"] == "model"]
    assert model_params and model_params[0]["kind"] == "model"


def test_custom_tool_appears_as_mcp_node(client, auth_headers):
    # A profile custom tool becomes a drag-in tool.custom__<name> node (category mcp).
    client.post(
        "/api/v1/tools/custom",
        json={
            "name": "wf_echo",
            "description": "echo",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            "endpoint": {"url": "https://example.invalid/echo", "method": "POST"},
            "enabled": True,
        },
        headers=auth_headers,
    )
    catalog = client.get("/api/v1/graph-workflows/node-types", headers=auth_headers).json()
    node = next((t for t in catalog if t["type"] == "tool.custom__wf_echo"), None)
    assert node is not None, "custom tool should appear as a graph node"
    assert node["category"] == "mcp"


def test_workflow_crud_and_versioning(client, auth_headers):
    graph = {"nodes": [{"id": "n1", "type": "manual"}], "edges": []}
    resp = client.post(
        "/api/v1/graph-workflows",
        json={"name": "My flow", "description": "d", "graph": graph},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    wf = resp.json()
    assert wf["version"] == 1 and wf["active"] is False

    # Update graph → version bumps.
    graph["nodes"].append({"id": "n2", "type": "set", "params": {"fields": {"x": 1}}})
    graph["edges"].append({"id": "e1", "source": "n1", "target": "n2"})
    resp = client.patch(f"/api/v1/graph-workflows/{wf['id']}", json={"graph": graph}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["version"] == 2

    versions = client.get(f"/api/v1/graph-workflows/{wf['id']}/versions", headers=auth_headers).json()
    assert [v["version"] for v in versions] == [2, 1]

    # Roll back to v1.
    resp = client.post(f"/api/v1/graph-workflows/{wf['id']}/versions/1/restore", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["graph"]["nodes"]) == 1


def test_run_tool_and_set_nodes(client, auth_headers, captured_spawns):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "calc", "type": "tool.calculator", "params": {"expression": "6*7"}},
            {"id": "out", "type": "set", "params": {"fields": {"answer": "={{ $node.calc.output.result }}"}}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "calc"},
            {"id": "e2", "source": "calc", "target": "out"},
        ],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "calc flow", "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]

    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["calc"]["status"] == "ok"
    assert "42" in str(by_node["calc"]["output"]["result"])
    assert by_node["out"]["output"]["answer"].strip().startswith("42")


def test_node_outputs_history_and_export(client, auth_headers, captured_spawns):
    """Every executed value is persisted: /node-outputs surfaces the latest
    recorded output per node across past runs (feeds the edge inspector), and
    /export returns a portable, re-importable snapshot."""
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "calc", "type": "tool.calculator", "params": {"expression": "={{ $trigger.expr }}"}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "calc"}],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "history flow", "graph": graph}, headers=auth_headers
    ).json()

    # No runs yet → empty history.
    hist = client.get(f"/api/v1/graph-workflows/{wf['id']}/node-outputs", headers=auth_headers).json()
    assert hist == {}

    for expr in ("6*7", "10*10"):
        client.post(
            f"/api/v1/graph-workflows/{wf['id']}/run",
            json={"payload": {"expr": expr}},
            headers=auth_headers,
        )
        _drive_last_run(captured_spawns)

    hist = client.get(f"/api/v1/graph-workflows/{wf['id']}/node-outputs", headers=auth_headers).json()
    assert set(hist) >= {"t", "calc"}
    # The latest run wins: 10*10, not 6*7.
    assert "100" in str(hist["calc"]["output"]["result"])
    assert hist["calc"]["run_id"]
    assert hist["calc"]["finished_at"]

    exported = client.get(f"/api/v1/graph-workflows/{wf['id']}/export", headers=auth_headers).json()
    assert exported["kind"] == "spice-sibyl.graph-workflow"
    assert exported["name"] == "history flow"
    assert {n["id"] for n in exported["graph"]["nodes"]} == {"t", "calc"}
    # Round-trip: the export body is directly importable via POST /.
    reimported = client.post(
        "/api/v1/graph-workflows",
        json={"name": exported["name"], "description": exported["description"], "graph": exported["graph"]},
        headers=auth_headers,
    )
    assert reimported.status_code == 201


def test_if_branch_routes_and_skips(client, auth_headers, captured_spawns):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "gate", "type": "if", "params": {"condition": "={{ $trigger.count > 3 }}"}},
            {"id": "yes", "type": "set", "params": {"fields": {"branch": "yes"}}},
            {"id": "no", "type": "set", "params": {"fields": {"branch": "no"}}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "gate"},
            {"id": "e2", "source": "gate", "target": "yes", "sourceHandle": "true"},
            {"id": "e3", "source": "gate", "target": "no", "sourceHandle": "false"},
        ],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "if flow", "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {"count": 5}}, headers=auth_headers
    ).json()["run_id"]

    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed"
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["yes"]["status"] == "ok"
    assert by_node["no"]["status"] == "skipped"


def test_for_loop_iterates_body_and_collects(client, auth_headers, captured_spawns):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "loop", "type": "for", "params": {"items": "={{ $trigger.nums }}"}},
            {"id": "body", "type": "set", "params": {"fields": {"val": "={{ $item }}"}}},
            {"id": "collect", "type": "set", "params": {"fields": {"all": "={{ $node.loop.output.items }}"}}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "loop"},
            {"id": "e2", "source": "loop", "target": "body", "sourceHandle": "loop"},
            {"id": "e3", "source": "loop", "target": "collect", "sourceHandle": "done"},
        ],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "for flow", "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {"nums": [1, 2, 3]}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["loop"]["output"]["count"] == 3
    assert by_node["loop"]["output"]["items"] == [{"val": 1}, {"val": 2}, {"val": 3}]
    # The body node ran once per item (3 node_run rows).
    body_runs = [nr for nr in run["node_runs"] if nr["node_id"] == "body"]
    assert len(body_runs) == 3
    # Continuation on the 'done' handle sees the collected items.
    assert by_node["collect"]["output"]["all"] == [{"val": 1}, {"val": 2}, {"val": 3}]


def test_repeat_loop_runs_n_times(client, auth_headers, captured_spawns):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "loop", "type": "repeat", "params": {"times": 4}},
            {"id": "body", "type": "set", "params": {"fields": {"i": "={{ $index }}"}}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "loop"},
            {"id": "e2", "source": "loop", "target": "body", "sourceHandle": "loop"},
        ],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "repeat flow", "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["loop"]["output"]["count"] == 4
    assert by_node["loop"]["output"]["items"] == [{"i": 0}, {"i": 1}, {"i": 2}, {"i": 3}]


def test_webhook_trigger_creates_token(client, auth_headers):
    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": "hook flow", "graph": {"nodes": [{"id": "n1", "type": "webhook"}], "edges": []}},
        headers=auth_headers,
    ).json()
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "webhook", "config": {}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["token"]
    assert token

    # Inactive workflow → webhook rejected.
    assert client.post(f"/api/v1/wf/hooks/{token}", json={"x": 1}).status_code == 404
    # Activate, then the public hook fires a run.
    client.post(f"/api/v1/graph-workflows/{wf['id']}/activate", headers=auth_headers)
    resp = client.post(f"/api/v1/wf/hooks/{token}", json={"x": 1})
    assert resp.status_code == 200, resp.text
    assert "run_id" in resp.json()


def test_examples_catalog_is_valid_and_importable(client, auth_headers, captured_spawns):
    # The curated graph examples are served.
    resp = client.get("/api/v1/graph-workflows/examples", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    examples = resp.json()
    assert len(examples) == 6
    ids = {e["id"] for e in examples}
    assert {
        "rss-morning-digest", "weather-greeting", "webhook-kb-answer",
        "page-keyword-watch", "api-error-fallback", "subworkflow-composer",
    } == ids

    # CI guard: every node type used by an example must exist in the palette catalog
    # (this catches a tool rename or a dropped node kind before a user hits it).
    catalog = {t["type"] for t in client.get("/api/v1/graph-workflows/node-types", headers=auth_headers).json()}
    for ex in examples:
        for nt in ex["node_types"]:
            assert nt in catalog, f"example {ex['id']} uses unknown node type {nt}"
        for node in ex["graph"]["nodes"]:
            assert node["type"] in catalog, f"example {ex['id']} node {node['id']} type {node['type']} not in catalog"

    # One-click import = create a workflow from an example graph, then run it.
    weather = next(e for e in examples if e["id"] == "weather-greeting")
    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": weather["title"], "description": weather["description"], "graph": weather["graph"]},
        headers=auth_headers,
    ).json()
    assert len(wf["graph"]["nodes"]) == 3
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["message"]["status"] == "ok"
    assert "Milano" in by_node["message"]["output"]["message"]


# ── http.request / error branch / subworkflow (Phase 29.c) ──────────────────

class _FakeResponse:
    def __init__(self, status=200, text='{"ok": true}', ctype="application/json"):
        self.status_code = status
        self.is_success = 200 <= status < 300
        self.text = text
        self.headers = {"content-type": ctype}

    def json(self):
        import json as _json
        return _json.loads(self.text)


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient: constructed, entered, .request()ed."""

    def __init__(self, response):
        self._response = response
        self.calls: list[tuple] = []

    def __call__(self, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self._response


def test_http_request_node(client, auth_headers, captured_spawns, monkeypatch):
    import httpx

    fake = _FakeAsyncClient(_FakeResponse(text='{"version": "1.0"}'))
    monkeypatch.setattr(httpx, "AsyncClient", fake)
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "api", "type": "http.request",
             "params": {"method": "GET", "url": "https://api.test/health"}},
            {"id": "out", "type": "set", "params": {"fields": {
                "version": "={{ $node.api.output.json.version }}",
                "code": "={{ $node.api.output.status }}",
            }}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "api"},
            {"id": "e2", "source": "api", "target": "out"},
        ],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "http flow", "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]

    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["api"]["output"]["status"] == 200
    assert by_node["api"]["output"]["ok"] is True
    assert by_node["out"]["output"]["version"] == "1.0"
    assert fake.calls[0][0] == "GET"


def test_http_request_error_routes_to_error_branch(client, auth_headers, captured_spawns, monkeypatch):
    import httpx

    fake = _FakeAsyncClient(_FakeResponse(status=503, text="unavailable", ctype="text/plain"))
    monkeypatch.setattr(httpx, "AsyncClient", fake)
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "api", "type": "http.request", "retry": 1, "backoff": 0.0, "onError": "branch",
             "params": {"method": "GET", "url": "https://api.test/health"}},
            {"id": "ok", "type": "set", "params": {"fields": {"state": "up"}}},
            {"id": "alert", "type": "set", "params": {"fields": {
                "state": "down", "detail": "={{ $node.api.output.error }}",
            }}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "api"},
            {"id": "e2", "source": "api", "target": "ok", "sourceHandle": "main"},
            {"id": "e3", "source": "api", "target": "alert", "sourceHandle": "error"},
        ],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "fallback flow", "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]

    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run  # error branch handled the failure
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["api"]["status"] == "error"
    assert by_node["ok"]["status"] == "skipped"
    assert by_node["alert"]["status"] == "ok"
    assert "503" in by_node["alert"]["output"]["detail"]
    assert len(fake.calls) == 2  # initial attempt + one retry


def test_subworkflow_runs_child_and_returns_sink_output(client, auth_headers, captured_spawns):
    child_graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "echo", "type": "set", "params": {"fields": {"echo": "={{ $trigger.input }}"}}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "echo"}],
    }
    child = client.post(
        "/api/v1/graph-workflows", json={"name": "child", "graph": child_graph}, headers=auth_headers
    ).json()

    parent_graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "sub", "type": "subworkflow",
             "params": {"workflow_id": child["id"], "payload": {"input": "={{ $trigger.msg }}"}}},
            {"id": "wrap", "type": "set", "params": {"fields": {
                "result": "={{ $node.sub.output.output }}",
                "child_status": "={{ $node.sub.output.status }}",
            }}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "sub"},
            {"id": "e2", "source": "sub", "target": "wrap"},
        ],
    }
    parent = client.post(
        "/api/v1/graph-workflows", json={"name": "parent", "graph": parent_graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{parent['id']}/run", json={"payload": {"msg": "ciao"}}, headers=auth_headers
    ).json()["run_id"]

    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["sub"]["output"]["status"] == "completed"
    assert by_node["wrap"]["output"]["result"] == {"echo": "ciao"}
    assert by_node["wrap"]["output"]["child_status"] == "completed"

    # The child executed as its own observable run.
    child_runs = client.get(
        f"/api/v1/graph-workflows/{child['id']}/runs", headers=auth_headers
    ).json()
    assert child_runs and child_runs[0]["trigger_type"] == "subworkflow"
    assert child_runs[0]["status"] == "completed"


def test_subworkflow_depth_guard_blocks_recursion(client, auth_headers, captured_spawns):
    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": "ouroboros", "graph": {"nodes": [{"id": "t", "type": "manual"}], "edges": []}},
        headers=auth_headers,
    ).json()
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "self", "type": "subworkflow", "params": {"workflow_id": wf["id"]}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "self"}],
    }
    client.patch(f"/api/v1/graph-workflows/{wf['id']}", json={"graph": graph}, headers=auth_headers)
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]

    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "failed"
    assert "depth" in (run["error"] or "")


def test_unwired_non_trigger_node_is_skipped(client, auth_headers, captured_spawns):
    # A node dropped on the canvas but never connected must NOT fire at run
    # start — only trigger roots are entry points.
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "out", "type": "set", "params": {"fields": {"x": 1}}},
            {"id": "orphan", "type": "notify.inapp", "params": {"title": "boo", "body": "!"}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "out"}],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "orphan flow", "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["out"]["status"] == "ok"
    assert by_node["orphan"]["status"] == "skipped"


# ── run registry + notification nodes (Phase 29.d) ──────────────────────────

def test_profile_wide_run_registry(client, auth_headers, captured_spawns):
    graph = {"nodes": [{"id": "t", "type": "manual"},
                       {"id": "out", "type": "set", "params": {"fields": {"x": 1}}}],
             "edges": [{"id": "e1", "source": "t", "target": "out"}]}
    wf_a = client.post("/api/v1/graph-workflows", json={"name": "reg A", "graph": graph}, headers=auth_headers).json()
    wf_b = client.post("/api/v1/graph-workflows", json={"name": "reg B", "graph": graph}, headers=auth_headers).json()
    for wf in (wf_a, wf_b):
        client.post(f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers)
        _drive_last_run(captured_spawns)

    runs = client.get("/api/v1/graph-workflows/runs", headers=auth_headers).json()
    names = {r["workflow_name"] for r in runs}
    assert {"reg A", "reg B"} <= names
    assert all(r["status"] == "completed" for r in runs if r["workflow_name"] in ("reg A", "reg B"))

    only_a = client.get(
        f"/api/v1/graph-workflows/runs?workflow_id={wf_a['id']}", headers=auth_headers
    ).json()
    assert only_a and all(r["workflow_id"] == wf_a["id"] for r in only_a)

    none_failed = client.get(
        f"/api/v1/graph-workflows/runs?status=failed&workflow_id={wf_a['id']}", headers=auth_headers
    ).json()
    assert none_failed == []


def test_notify_inapp_node(client, auth_headers, captured_spawns):
    graph = {"nodes": [{"id": "t", "type": "manual"},
                       {"id": "n", "type": "notify.inapp",
                        "params": {"title": "Digest", "body": "={{ $trigger.msg }}"}}],
             "edges": [{"id": "e1", "source": "t", "target": "n"}]}
    wf = client.post("/api/v1/graph-workflows", json={"name": "inapp", "graph": graph}, headers=auth_headers).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {"msg": "hello"}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["n"]["output"]["channel"] == "inapp"


def test_notify_webhook_node_posts_payload(client, auth_headers, captured_spawns, monkeypatch):
    import httpx

    fake = _FakeAsyncClient(_FakeResponse(text='{"ok": true}'))
    monkeypatch.setattr(httpx, "AsyncClient", fake)
    graph = {"nodes": [{"id": "t", "type": "manual"},
                       {"id": "hook", "type": "notify.webhook",
                        "params": {"url": "https://hooks.test/x",
                                   "payload": {"text": "={{ $trigger.msg }}"}}}],
             "edges": [{"id": "e1", "source": "t", "target": "hook"}]}
    wf = client.post("/api/v1/graph-workflows", json={"name": "hook", "graph": graph}, headers=auth_headers).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {"msg": "ciao"}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    method, url, kw = fake.calls[0]
    assert (method, url) == ("POST", "https://hooks.test/x")
    assert kw["json"] == {"text": "ciao"}


def test_notify_telegram_without_link_uses_error_policy(client, auth_headers, captured_spawns):
    graph = {"nodes": [{"id": "t", "type": "manual"},
                       {"id": "tg", "type": "notify.telegram", "onError": "continue",
                        "params": {"text": "hi"}},
                       {"id": "after", "type": "set", "params": {"fields": {"done": True}}}],
             "edges": [{"id": "e1", "source": "t", "target": "tg"},
                       {"id": "e2", "source": "tg", "target": "after"}]}
    wf = client.post("/api/v1/graph-workflows", json={"name": "tg", "graph": graph}, headers=auth_headers).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run  # onError=continue absorbed the failure
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert "linked" in (by_node["tg"]["output"]["error"] or "")
    assert by_node["after"]["status"] == "ok"


def test_notify_email_requires_smtp_config(client, auth_headers, captured_spawns):
    graph = {"nodes": [{"id": "t", "type": "manual"},
                       {"id": "mail", "type": "notify.email",
                        "params": {"to": "x@example.com", "subject": "s", "body": "b"}}],
             "edges": [{"id": "e1", "source": "t", "target": "mail"}]}
    wf = client.post("/api/v1/graph-workflows", json={"name": "mail", "graph": graph}, headers=auth_headers).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "failed"
    assert "SMTP" in (run["error"] or "")


def test_cancel_run_from_registry(client, auth_headers, captured_spawns):
    graph = {"nodes": [{"id": "t", "type": "manual"},
                       {"id": "out", "type": "set", "params": {"fields": {"x": 1}}}],
             "edges": [{"id": "e1", "source": "t", "target": "out"}]}
    wf = client.post("/api/v1/graph-workflows", json={"name": "to cancel", "graph": graph}, headers=auth_headers).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]

    # _spawn is intercepted, so the run row sits in 'pending' — cancel flips it.
    resp = client.post(f"/api/v1/graph-workflows/runs/{run_id}/cancel", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"

    # A terminal run can't be cancelled again.
    resp = client.post(f"/api/v1/graph-workflows/runs/{run_id}/cancel", headers=auth_headers)
    assert resp.status_code == 409


def test_schedule_trigger_sets_next_run(client, auth_headers):
    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": "sched flow", "graph": {"nodes": [{"id": "n1", "type": "schedule"}], "edges": []}},
        headers=auth_headers,
    ).json()
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "schedule", "config": {"recurrence": "daily"}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["next_run_at"] is not None
