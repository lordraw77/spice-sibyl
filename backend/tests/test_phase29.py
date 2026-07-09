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
    # The 4 curated graph examples are served.
    resp = client.get("/api/v1/graph-workflows/examples", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    examples = resp.json()
    assert len(examples) == 4
    ids = {e["id"] for e in examples}
    assert {"rss-morning-digest", "weather-greeting", "webhook-kb-answer", "page-keyword-watch"} == ids

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
