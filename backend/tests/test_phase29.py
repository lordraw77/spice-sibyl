"""
Phase 29 — visual node-graph workflow engine.

Unit tests for the standalone expression resolver plus end-to-end API tests for
the DAG engine (CRUD, versioning, triggers, a deterministic run over tool/set/if
nodes driven by the mock provider).
"""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.config import settings
from app.services import expression_resolver as er
from app.services import workflow_graph_service as engine


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone or "UTC")


@pytest.fixture()
def captured_spawns(monkeypatch):
    """The TestClient runs each request on a short-lived loop that cancels
    fire-and-forget tasks, so intercept ``_spawn`` and let the test drive
    ``_execute`` itself (mirrors the Phase 18 ``no_autostart`` fixture)."""
    spawns: list[tuple] = []
    monkeypatch.setattr(engine, "_spawn", lambda *args, **kwargs: spawns.append((args, kwargs)))
    return spawns


def _drive_last_run(spawns):
    """Execute the most recently spawned run synchronously to completion."""
    args, kwargs = spawns[-1]
    asyncio.run(engine._execute(*args, **kwargs))


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


def test_surrounding_whitespace_keeps_native_type():
    ctx = _ctx()
    # A trailing newline (stray Enter in the inspector textarea) must not
    # stringify a native result — the classic broken-for-each slip.
    assert asyncio.run(er.resolve_value("={{ $node.n1.output.items }}\n", ctx)) == [1, 2, 3]
    assert asyncio.run(er.resolve_value("  {{ $trigger.count }}\n ", ctx)) == 5
    # Real surrounding text still stitches to a string.
    assert asyncio.run(er.resolve_value("n: ={{ $trigger.count }}\n", ctx)) == "n: 5\n"


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


def test_extract_usage_reads_provider_token_counts():
    assert engine._extract_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}) == {
        "tokens_in": 10, "tokens_out": 5, "tokens_total": 15,
    }
    assert engine._extract_usage({}) is None
    assert engine._extract_usage({"usage": {}}) is None


def test_llm_completion_node_reports_usage_key(client, auth_headers, captured_spawns):
    # The mock provider (no API key needed) doesn't report token usage, so _usage
    # is present but null — proving the field is always surfaced, cost is never guessed.
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "llm", "type": "llm.completion", "params": {"model": "mock/spice-sibyl-1", "prompt": "hi"}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "llm"}],
    }
    wf = client.post("/api/v1/graph-workflows", json={"name": "llm usage", "graph": graph}, headers=auth_headers).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert "_usage" in by_node["llm"]["output"]


def test_llm_completion_node_uses_response_cache_on_repeat_run(client, auth_headers, captured_spawns):
    from app.services import cache_service

    cache_service.clear()
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "llm", "type": "llm.completion",
             "params": {"model": "mock/spice-sibyl-cache", "prompt": "same prompt every time"}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "llm"}],
    }
    wf = client.post("/api/v1/graph-workflows", json={"name": "llm cache", "graph": graph}, headers=auth_headers).json()

    def _run_once():
        run_id = client.post(
            f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
        ).json()["run_id"]
        _drive_last_run(captured_spawns)
        run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
        assert run["status"] == "completed", run
        return {nr["node_id"]: nr for nr in run["node_runs"]}["llm"]["output"]

    first = _run_once()
    assert first["_cache"] == "miss"
    second = _run_once()
    assert second["_cache"] == "hit"
    assert second["content"] == first["content"]


def test_llm_completion_node_failover_chain_falls_back_to_next_model(
    client, auth_headers, captured_spawns, monkeypatch
):
    from app.services.provider_factory import ProviderFactory

    real_get_provider = ProviderFactory.get_provider

    def _fake_get_provider(model: str):
        if model == "mock/broken":
            raise RuntimeError("provider unavailable")
        return real_get_provider(model)

    monkeypatch.setattr(ProviderFactory, "get_provider", staticmethod(_fake_get_provider))

    resp = client.put(
        "/api/v1/admin/model-failover-chains",
        json={"chains": {"my-chain": ["mock/broken", "mock/spice-sibyl-2"]}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Regression check for the exact path the workflow designer's dropdown calls
    # (frontend GraphWorkflowService.failoverChains() -> GET /v1/models/failover-chains,
    # NOT /v1/model-failover-chains — models.router is mounted with prefix "/models").
    read_resp = client.get("/api/v1/models/failover-chains", headers=auth_headers)
    assert read_resp.status_code == 200, read_resp.text
    assert read_resp.json()["chains"]["my-chain"] == ["mock/broken", "mock/spice-sibyl-2"]

    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "llm", "type": "llm.completion",
             "params": {"model": "mock/broken", "prompt": "hi", "failover_chain": "my-chain"}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "llm"}],
    }
    wf = client.post("/api/v1/graph-workflows", json={"name": "llm failover", "graph": graph}, headers=auth_headers).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    output = {nr["node_id"]: nr for nr in run["node_runs"]}["llm"]["output"]
    assert output["model"] == "mock/spice-sibyl-2"
    assert output["_failover"] == {"tried": ["mock/broken", "mock/spice-sibyl-2"], "used": "mock/spice-sibyl-2"}


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


def test_loop_body_node_sees_same_iteration_outputs(client, auth_headers, captured_spawns):
    """Inside an iteration, a body node can read an earlier body node's output
    as $node.<id>.output — the path the editor's field chooser produces."""
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "loop", "type": "for", "params": {"items": "={{ $trigger.servers }}"}},
            {"id": "pick", "type": "set", "params": {"fields": "={{ $item }}"}},
            {"id": "fmt", "type": "set", "params": {"fields": {"line": "={{ $node.pick.output.host }}"}}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "loop"},
            {"id": "e2", "source": "loop", "target": "pick", "sourceHandle": "loop"},
            {"id": "e3", "source": "pick", "target": "fmt"},
        ],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "body ctx flow", "graph": graph}, headers=auth_headers
    ).json()
    servers = [{"host": "10.0.0.1"}, {"host": "10.0.0.2"}]
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {"servers": servers}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    # Each iteration's sink saw THAT iteration's pick output, not a stale one.
    assert by_node["loop"]["output"]["items"] == [{"line": "10.0.0.1"}, {"line": "10.0.0.2"}]


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


def test_wait_node_sleeps_and_reports_duration(client, auth_headers, captured_spawns):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "w", "type": "wait", "params": {"seconds": 0}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "w"}],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "wait flow", "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["w"]["output"]["waited"] == 0


def test_aggregate_node_reduces_field(client, auth_headers, captured_spawns):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {
                "id": "agg", "type": "aggregate",
                "params": {"items": "={{ $trigger.rows }}", "op": "sum", "field": "price"},
            },
        ],
        "edges": [{"id": "e1", "source": "t", "target": "agg"}],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "aggregate flow", "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run",
        json={"payload": {"rows": [{"price": 10}, {"price": 5}, {"price": 3}]}},
        headers=auth_headers,
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["agg"]["output"] == {"result": 18, "count": 3}


def test_batch_node_splits_array(client, auth_headers, captured_spawns):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "b", "type": "batch", "params": {"items": "={{ $trigger.items }}", "size": 2}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "b"}],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "batch flow", "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run",
        json={"payload": {"items": [1, 2, 3, 4, 5]}},
        headers=auth_headers,
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["b"]["output"] == {"batches": [[1, 2], [3, 4], [5]], "count": 3}


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
    assert len(examples) == 32
    ids = {e["id"] for e in examples}
    assert {
        "rss-morning-digest", "rss-to-slack-alert", "weather-greeting", "webhook-kb-answer",
        "page-keyword-watch", "api-error-fallback", "subworkflow-composer",
        "switch-routing", "fanout-merge", "orders-filter-total", "batch-loop",
        "poll-wait-repeat", "python-transform", "event-inapp-alert",
        "notify-broadcast", "agent-research-brief", "error-alert-hub",
        "approval-gate-deploy", "ticket-triage-classify",
        "kb-search-rag", "paginate-while", "extract-to-db", "file-read-report",
        "parse-http-json", "chatbot-reply", "success-pipeline",
        "expense-approval-form", "payment-webhook-wait", "http-mock-pin-demo",
        "idempotent-order-saga", "nightly-report-blackout", "llm-quality-gate",
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


def test_notify_telegram_invalid_parse_mode_routes_to_error(client, auth_headers, captured_spawns):
    graph = {"nodes": [{"id": "t", "type": "manual"},
                       {"id": "tg", "type": "notify.telegram", "onError": "continue",
                        "params": {"text": "hi", "parse_mode": "bogus"}}],
             "edges": [{"id": "e1", "source": "t", "target": "tg"}]}
    wf = client.post("/api/v1/graph-workflows", json={"name": "tg bad mode", "graph": graph}, headers=auth_headers).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert "parse_mode" in (by_node["tg"]["output"]["error"] or "")


def test_notify_telegram_parse_mode_normalizes_bold_and_forwards(client, auth_headers, captured_spawns, monkeypatch):
    from app.db import telegram_link_repository
    from app.services import notification_service, workflow_graph_service as engine

    calls = []

    async def _fake_get_link(db, profile_id):
        return {"telegram_id": 123}

    async def _fake_notify(db, profile_id, event_type, text, parse_mode=None):
        calls.append((text, parse_mode))

    monkeypatch.setattr(telegram_link_repository, "get_by_profile_id", _fake_get_link)
    monkeypatch.setattr(notification_service, "notify_telegram", _fake_notify)

    graph = {"nodes": [{"id": "t", "type": "manual"},
                       {"id": "tg", "type": "notify.telegram",
                        "params": {"text": "**bold** and *already single*", "parse_mode": "Markdown"}}],
             "edges": [{"id": "e1", "source": "t", "target": "tg"}]}
    wf = client.post("/api/v1/graph-workflows", json={"name": "tg md", "graph": graph}, headers=auth_headers).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    assert calls == [("*bold* and *already single*", "Markdown")]
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["tg"]["output"]["parse_mode"] == "Markdown"


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


def test_run_completes_under_a_low_concurrency_cap(client, auth_headers, captured_spawns, monkeypatch):
    from app.services import workflow_graph_service as engine

    monkeypatch.setattr(engine.settings, "graph_workflow_max_concurrent_nodes", 1)
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "a", "type": "set", "params": {"fields": {"v": "a"}}},
            {"id": "b", "type": "set", "params": {"fields": {"v": "b"}}},
            {"id": "c", "type": "set", "params": {"fields": {"v": "c"}}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "a"},
            {"id": "e2", "source": "t", "target": "b"},
            {"id": "e3", "source": "t", "target": "c"},
        ],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "capped flow", "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert {by_node[n]["output"]["v"] for n in ("a", "b", "c")} == {"a", "b", "c"}


def test_workflow_raises_alert_after_repeated_run_failures(client, auth_headers, captured_spawns, monkeypatch):
    from app.services import workflow_graph_service as engine

    monkeypatch.setattr(engine.settings, "graph_workflow_run_failure_alert_threshold", 2)
    # An http.request with a non-http(s) URL raises synchronously in _exec_http_request
    # — a fast, deterministic way to fail a run without touching the network.
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "bad", "type": "http.request", "params": {"url": "not-a-url"}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "bad"}],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "flaky flow", "graph": graph}, headers=auth_headers
    ).json()

    for _ in range(2):
        client.post(f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers)
        _drive_last_run(captured_spawns)

    notifications = client.get("/api/v1/notifications", headers=auth_headers).json()
    assert any("errore ripetuto" in n["title"].lower() or "ripetut" in n["body"].lower()
               for n in notifications["items"])


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


def test_webhook_signature_enforced_after_rotate_secret(client, auth_headers):
    import hashlib
    import hmac

    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": "signed hook flow", "graph": {"nodes": [{"id": "n1", "type": "webhook"}], "edges": []}},
        headers=auth_headers,
    ).json()
    tid = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "webhook", "config": {}},
        headers=auth_headers,
    ).json()["id"]
    token = client.get(f"/api/v1/graph-workflows/{wf['id']}/triggers", headers=auth_headers).json()[0]["token"]
    client.post(f"/api/v1/graph-workflows/{wf['id']}/activate", headers=auth_headers)

    # No secret yet → open, as before.
    assert client.post(f"/api/v1/wf/hooks/{token}", json={"x": 1}).status_code == 200

    secret = client.post(
        f"/api/v1/graph-workflows/triggers/{tid}/rotate-secret", headers=auth_headers
    ).json()["secret"]
    assert secret

    body = b'{"x": 1}'
    # Missing signature is rejected.
    resp = client.post(f"/api/v1/wf/hooks/{token}", content=body, headers={"content-type": "application/json"})
    assert resp.status_code == 401
    # Wrong signature is rejected.
    resp = client.post(
        f"/api/v1/wf/hooks/{token}", content=body,
        headers={"content-type": "application/json", "x-signature": "sha256=deadbeef"},
    )
    assert resp.status_code == 401
    # Correct signature succeeds.
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    resp = client.post(
        f"/api/v1/wf/hooks/{token}", content=body,
        headers={"content-type": "application/json", "x-signature": sig},
    )
    assert resp.status_code == 200, resp.text


def test_event_trigger_auto_disables_after_repeated_failures(client, auth_headers, monkeypatch):
    from app.services import workflow_graph_service as engine

    monkeypatch.setattr(engine.settings, "graph_workflow_trigger_max_failures", 2)

    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": "flaky event flow", "graph": {"nodes": [{"id": "n1", "type": "event"}], "edges": []}},
        headers=auth_headers,
    ).json()
    tid = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "event", "config": {"event": "doc.ingested"}},
        headers=auth_headers,
    ).json()["id"]
    client.post(f"/api/v1/graph-workflows/{wf['id']}/activate", headers=auth_headers)

    # run_workflow is what actually fails here (no DB row-model reason to fail
    # otherwise) — force it to raise so the failure path is exercised deterministically.
    async def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(engine, "run_workflow", _boom)

    for _ in range(2):
        asyncio.run(engine.dispatch_event("doc.ingested", {}))

    trigger = client.get(f"/api/v1/graph-workflows/{wf['id']}/triggers", headers=auth_headers).json()[0]
    assert trigger["fail_count"] == 2
    assert trigger["enabled"] is False
    assert "boom" in (trigger["last_error"] or "")


def test_schedules_overview_lists_triggers_across_workflows(client, auth_headers, captured_spawns):
    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": "overview flow", "graph": {"nodes": [{"id": "n1", "type": "schedule"}], "edges": []}},
        headers=auth_headers,
    ).json()
    client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "schedule", "config": {"recurrence": "daily"}},
        headers=auth_headers,
    )
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)

    schedules = client.get("/api/v1/graph-workflows/schedules", headers=auth_headers).json()
    row = next(s for s in schedules if s["workflow_id"] == wf["id"])
    assert row["trigger_type"] == "schedule"
    assert row["next_run_at"] is not None
    assert row["last_run_status"] == "completed"
    assert row["last_run_at"] is not None
    assert row["fail_count"] == 0


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


def test_schedule_trigger_daily_pattern_uses_given_time(client, auth_headers):
    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": "daily flow", "graph": {"nodes": [{"id": "n1", "type": "schedule"}], "edges": []}},
        headers=auth_headers,
    ).json()
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "schedule", "config": {"pattern": "daily", "time": "09:30"}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["config"]["recurrence"] == "daily"
    fire = datetime.fromtimestamp(body["next_run_at"], _tz())
    assert (fire.hour, fire.minute) == (9, 30)


def test_schedule_trigger_weekly_pattern_multiple_days(client, auth_headers):
    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": "weekly flow", "graph": {"nodes": [{"id": "n1", "type": "schedule"}], "edges": []}},
        headers=auth_headers,
    ).json()
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "schedule", "config": {"pattern": "weekly", "weekdays": ["mon", "wed", "fri"], "time": "14:00"}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["config"]["recurrence"] == "weekly:mon,wed,fri"
    assert body["next_run_at"] is not None

    # No weekday selected → rejected.
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "schedule", "config": {"pattern": "weekly", "weekdays": [], "time": "14:00"}},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_schedule_trigger_cron_pattern_and_validation(client, auth_headers):
    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": "cron flow", "graph": {"nodes": [{"id": "n1", "type": "schedule"}], "edges": []}},
        headers=auth_headers,
    ).json()
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "schedule", "config": {"pattern": "cron", "cron": "*/15 * * * *"}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["config"]["recurrence"] == "cron:*/15,*,*,*,*"

    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "schedule", "config": {"pattern": "cron", "cron": "not a cron"}},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_schedule_trigger_once_pattern_with_explicit_date(client, auth_headers):
    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": "once flow", "graph": {"nodes": [{"id": "n1", "type": "schedule"}], "edges": []}},
        headers=auth_headers,
    ).json()
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "schedule", "config": {"pattern": "once", "date": "2030-01-01", "time": "08:00"}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["config"]["recurrence"] == "once"
    fire = datetime.fromtimestamp(body["next_run_at"], _tz())
    assert (fire.year, fire.month, fire.day) == (2030, 1, 1)


# ── partial runs + expression preview ───────────────────────────────────────

def _three_step_wf(client, auth_headers):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "a", "type": "set", "params": {"fields": {"x": 10}}},
            {"id": "b", "type": "set", "params": {"fields": {"y": "={{ $node.a.output.x * 2 }}"}}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "a"},
            {"id": "e2", "source": "a", "target": "b"},
        ],
    }
    return client.post(
        "/api/v1/graph-workflows", json={"name": "partial flow", "graph": graph}, headers=auth_headers
    ).json()


def test_partial_run_reuses_upstream_outputs(client, auth_headers, captured_spawns):
    wf = _three_step_wf(client, auth_headers)
    # Full run first: persists every node's output.
    client.post(f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers)
    _drive_last_run(captured_spawns)

    # Partial run from `b`: upstream `t`/`a` must not re-execute, and `b` must
    # still resolve $node.a.output.x from the seeded historical output.
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run",
        json={"payload": {}, "start_node_id": "b"},
        headers=auth_headers,
    ).json()["run_id"]
    _drive_last_run(captured_spawns)

    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    assert run["trigger_type"] == "partial"
    executed = {nr["node_id"] for nr in run["node_runs"]}
    assert executed == {"b"}, executed
    b = next(nr for nr in run["node_runs"] if nr["node_id"] == "b")
    assert b["output"] == {"y": 20}


def test_partial_run_unknown_start_node_is_400(client, auth_headers, captured_spawns):
    wf = _three_step_wf(client, auth_headers)
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run",
        json={"payload": {}, "start_node_id": "nope"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_expression_preview_against_latest_run(client, auth_headers, captured_spawns):
    wf = _three_step_wf(client, auth_headers)
    client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run",
        json={"payload": {"who": "world"}}, headers=auth_headers,
    )
    _drive_last_run(captured_spawns)

    def preview(expr):
        return client.post(
            f"/api/v1/graph-workflows/{wf['id']}/preview-expression",
            json={"expression": expr}, headers=auth_headers,
        ).json()

    assert preview("={{ $node.a.output.x + 5 }}") == {"ok": True, "value": 15}
    assert preview("={{ $trigger.who }}") == {"ok": True, "value": "world"}
    # Bad expressions surface the message inline instead of erroring the request.
    bad = preview("={{ open('/etc/passwd') }}")
    assert bad["ok"] is False and bad["error"]
    # Plain strings pass through untouched.
    assert preview("hello") == {"ok": True, "value": "hello"}


# ── Phase 30.f: per-node timeout ─────────────────────────────────────────────

def test_node_timeout_aborts_slow_attempt(client, auth_headers, captured_spawns):
    # A wait node asked to sleep 30s but capped at 50 ms times out; with the
    # default onError=stop the node errors and the run fails — proving the cap
    # fires without actually sleeping 30 seconds.
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "slow", "type": "wait", "params": {"seconds": 30}, "timeoutMs": 50},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "slow"}],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "timeout flow", "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "failed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["slow"]["status"] == "error"
    assert "timed out" in (by_node["slow"]["error"] or "")


def test_node_timeout_continue_on_error_keeps_run_alive(client, auth_headers, captured_spawns):
    # A timed-out node with onError=continue surfaces {error} on main and the
    # run still completes — the timeout obeys the normal failure policy.
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "slow", "type": "wait", "params": {"seconds": 30}, "timeoutMs": 50, "onError": "continue"},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "slow"}],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "timeout continue", "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert "timed out" in str(by_node["slow"]["output"])


# ── Phase 30.f: run replay ───────────────────────────────────────────────────

def _echo_trigger_wf(client, auth_headers):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "echo", "type": "set", "params": {"fields": {"who": "={{ $trigger.who }}"}}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "echo"}],
    }
    return client.post(
        "/api/v1/graph-workflows", json={"name": "echo flow", "graph": graph}, headers=auth_headers
    ).json()


def test_replay_reuses_original_trigger_payload(client, auth_headers, captured_spawns):
    wf = _echo_trigger_wf(client, auth_headers)
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run",
        json={"payload": {"who": "alice"}}, headers=auth_headers,
    ).json()["run_id"]
    _drive_last_run(captured_spawns)

    resp = client.post(f"/api/v1/graph-workflows/runs/{run_id}/replay", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    replay_id = resp.json()["run_id"]
    assert replay_id != run_id
    _drive_last_run(captured_spawns)

    replay = client.get(f"/api/v1/graph-workflows/runs/{replay_id}", headers=auth_headers).json()
    assert replay["status"] == "completed", replay
    assert replay["trigger_type"] == "manual"
    by_node = {nr["node_id"]: nr for nr in replay["node_runs"]}
    assert by_node["echo"]["output"]["who"] == "alice"


def test_replay_rejects_partial_run(client, auth_headers, captured_spawns):
    wf = _three_step_wf(client, auth_headers)
    # A partial run has no full trigger payload, so it can't be replayed.
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run",
        json={"payload": {}, "start_node_id": "b"}, headers=auth_headers,
    ).json()["run_id"]
    resp = client.post(f"/api/v1/graph-workflows/runs/{run_id}/replay", headers=auth_headers)
    assert resp.status_code == 409, resp.text


# ── Phase 32 (roadmap fase 1): $vars + $secrets ──────────────────────────────

def test_workflow_variables_resolve_in_run(client, auth_headers, captured_spawns):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "s", "type": "set", "params": {"fields": {"msg": "={{ $vars.greeting }} world"}}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "s"}],
    }
    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": "vars flow", "graph": graph, "variables": {"greeting": "hello"}},
        headers=auth_headers,
    ).json()
    assert wf["variables"] == {"greeting": "hello"}

    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)

    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["s"]["output"]["msg"] == "hello world"


def test_variables_update_does_not_bump_version(client, auth_headers):
    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": "v flow", "graph": {"nodes": [], "edges": []}},
        headers=auth_headers,
    ).json()
    assert wf["version"] == 1

    resp = client.patch(
        f"/api/v1/graph-workflows/{wf['id']}",
        json={"variables": {"a": 1, "b": "two"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["variables"] == {"a": 1, "b": "two"}
    assert body["version"] == 1  # only graph changes snapshot a new version


def test_export_includes_variables(client, auth_headers):
    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": "exp flow", "graph": {"nodes": [], "edges": []}, "variables": {"k": "v"}},
        headers=auth_headers,
    ).json()
    snap = client.get(f"/api/v1/graph-workflows/{wf['id']}/export", headers=auth_headers).json()
    assert snap["variables"] == {"k": "v"}
    # Round-trip: the export body imports back with its variables.
    wf2 = client.post("/api/v1/graph-workflows", json=snap, headers=auth_headers)
    assert wf2.status_code == 201
    assert wf2.json()["variables"] == {"k": "v"}


def test_secrets_crud_never_returns_value(client, auth_headers):
    resp = client.put(
        "/api/v1/graph-workflows/secrets",
        json={"name": "API_TOKEN", "value": "super-secret-token"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "API_TOKEN"
    assert "value" not in body

    listed = client.get("/api/v1/graph-workflows/secrets", headers=auth_headers).json()
    names = [s["name"] for s in listed]
    assert "API_TOKEN" in names
    assert all("value" not in s and "value_encrypted" not in s for s in listed)

    assert client.delete("/api/v1/graph-workflows/secrets/API_TOKEN", headers=auth_headers).status_code == 204
    assert client.delete("/api/v1/graph-workflows/secrets/API_TOKEN", headers=auth_headers).status_code == 404


def test_secret_name_validation(client, auth_headers):
    resp = client.put(
        "/api/v1/graph-workflows/secrets",
        json={"name": "bad name!", "value": "x"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_secret_resolves_in_run_but_masked_in_preview(client, auth_headers, captured_spawns):
    client.put(
        "/api/v1/graph-workflows/secrets",
        json={"name": "MY_KEY", "value": "s3cr3t-value"},
        headers=auth_headers,
    )
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "s", "type": "set", "params": {"fields": {"auth": "Bearer ={{ $secrets.MY_KEY }}"}}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "s"}],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "secret flow", "graph": graph}, headers=auth_headers
    ).json()

    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)

    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["s"]["output"]["auth"] == "Bearer s3cr3t-value"

    # The editor's expression preview must never reveal the plaintext.
    preview = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/preview-expression",
        json={"expression": "={{ $secrets.MY_KEY }}"},
        headers=auth_headers,
    ).json()
    assert preview["ok"] is True
    assert preview["value"] == "***"

    client.delete("/api/v1/graph-workflows/secrets/MY_KEY", headers=auth_headers)


# ── Phase 33 (roadmap fase 2): engine reliability ────────────────────────────

def _drive_run(spawns, run_id):
    """Execute a specific captured spawn (by run id) synchronously."""
    for args, kwargs in spawns:
        if args[0] == run_id:
            asyncio.run(engine._execute(*args, **kwargs))
            return
    raise AssertionError(f"run {run_id} was never spawned")


def test_backoff_strategy_validation(client, auth_headers):
    graph = {"nodes": [{"id": "n", "type": "manual", "backoffStrategy": "bogus"}], "edges": []}
    resp = client.post(
        "/api/v1/graph-workflows", json={"name": "bad backoff", "graph": graph}, headers=auth_headers
    )
    assert resp.status_code == 422


def test_exponential_backoff_sleeps_grow(client, auth_headers, captured_spawns, monkeypatch):
    # 2.1 — a failing node with backoffStrategy=exponential sleeps backoff * 2^attempt.
    delays: list[float] = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {
                "id": "boom", "type": "http.request",
                "params": {"url": "not-a-url"},
                "retry": 2, "backoff": 1, "backoffStrategy": "exponential",
                "onError": "continue",
            },
        ],
        "edges": [{"id": "e1", "source": "t", "target": "boom"}],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "backoff flow", "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)

    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run  # onError=continue swallows the failure
    assert delays == [1, 2]  # attempt 0 → 1s, attempt 1 → 2s


def test_node_catalog_exposes_error_trigger_and_retry_defaults(client, auth_headers):
    catalog = client.get("/api/v1/graph-workflows/node-types", headers=auth_headers).json()
    by_type = {t["type"]: t for t in catalog}
    assert "error" in by_type and by_type["error"]["category"] == "trigger"
    http_defaults = by_type["http.request"]["defaults"]
    assert http_defaults["retry"] == 2 and http_defaults["backoffStrategy"] == "exponential"
    assert by_type["llm.completion"]["defaults"]["retry"] == 1


def test_max_concurrent_runs_crud_and_export(client, auth_headers):
    # 2.3 — the limit persists, PATCHes without a version bump, and travels with exports.
    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": "limited flow", "graph": {"nodes": [], "edges": []}, "max_concurrent_runs": 2},
        headers=auth_headers,
    ).json()
    assert wf["max_concurrent_runs"] == 2 and wf["version"] == 1

    body = client.patch(
        f"/api/v1/graph-workflows/{wf['id']}", json={"max_concurrent_runs": 3}, headers=auth_headers
    ).json()
    assert body["max_concurrent_runs"] == 3 and body["version"] == 1

    snap = client.get(f"/api/v1/graph-workflows/{wf['id']}/export", headers=auth_headers).json()
    assert snap["max_concurrent_runs"] == 3
    wf2 = client.post("/api/v1/graph-workflows", json=snap, headers=auth_headers).json()
    assert wf2["max_concurrent_runs"] == 3


def test_runs_beyond_limit_queue_and_promote(client, auth_headers, captured_spawns):
    # 2.3 — with max_concurrent_runs=1 the second run queues; finishing the first
    # promotes it FIFO (the engine re-spawns it from its parked trigger payload).
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "s", "type": "set", "params": {"fields": {"who": "={{ $trigger.who }}"}}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "s"}],
    }
    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": "queue flow", "graph": graph, "max_concurrent_runs": 1},
        headers=auth_headers,
    ).json()

    first = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {"who": "one"}}, headers=auth_headers
    ).json()["run_id"]
    second = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {"who": "two"}}, headers=auth_headers
    ).json()["run_id"]

    assert len(captured_spawns) == 1  # the second run must not have spawned
    second_row = client.get(f"/api/v1/graph-workflows/runs/{second}", headers=auth_headers).json()
    assert second_row["status"] == "queued"

    _drive_run(captured_spawns, first)  # finishing the first promotes the queued one
    assert len(captured_spawns) == 2
    _drive_run(captured_spawns, second)

    for run_id, who in ((first, "one"), (second, "two")):
        run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
        assert run["status"] == "completed", run
        by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
        assert by_node["s"]["output"]["who"] == who  # the parked payload survived the queue


def test_queued_run_can_be_cancelled(client, auth_headers, captured_spawns):
    graph = {"nodes": [{"id": "t", "type": "manual"}], "edges": []}
    wf = client.post(
        "/api/v1/graph-workflows",
        json={"name": "queue cancel flow", "graph": graph, "max_concurrent_runs": 1},
        headers=auth_headers,
    ).json()
    client.post(f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers)
    queued = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    resp = client.post(f"/api/v1/graph-workflows/runs/{queued}/cancel", headers=auth_headers)
    assert resp.status_code == 200
    assert client.get(
        f"/api/v1/graph-workflows/runs/{queued}", headers=auth_headers
    ).json()["status"] == "cancelled"


def test_resume_interrupted_run_skips_checkpointed_nodes(client, auth_headers, captured_spawns):
    # 2.4 — a run left 'running' resumes from its checkpoint: nodes with a
    # persisted {output, handles} entry don't re-execute, downstream expressions
    # still resolve against their checkpointed outputs.
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "a", "type": "set", "params": {"fields": {"x": 41}}},
            {"id": "b", "type": "set", "params": {"fields": {"y": "={{ $node.a.output.x + 1 }}"}}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "a"},
            {"id": "e2", "source": "a", "target": "b"},
        ],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "resume flow", "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]

    async def simulate_crash_mid_run():
        from app.db import graph_workflow_repository as repo
        db = await engine._connect()
        try:
            # t and a completed (checkpoint incl. handles); b never started.
            await repo.set_run_status(db, run_id, "running", context={
                "node": {
                    "t": {"output": {}, "handles": ["main"]},
                    "a": {"output": {"x": 41}, "handles": ["main"]},
                },
                "trigger": {},
            })
        finally:
            await db.close()

    asyncio.run(simulate_crash_mid_run())
    before = len(captured_spawns)
    asyncio.run(engine.resume_interrupted_runs())
    assert any(args[0] == run_id for args, _ in captured_spawns[before:])
    _drive_run(captured_spawns[before:], run_id)

    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    executed = [nr["node_id"] for nr in run["node_runs"] if nr["status"] == "ok"]
    assert executed == ["b"]  # only the missing node ran
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["b"]["output"]["y"] == 42  # resolved from the checkpointed output


def test_error_trigger_fires_with_failure_payload(client, auth_headers, captured_spawns):
    # 2.5 — when a run fails, active workflows with an `error` trigger fire and
    # receive {workflow_id, workflow_name, run_id, error, failed_node}.
    handler_graph = {
        "nodes": [
            {"id": "err", "type": "error"},
            {"id": "cap", "type": "set", "params": {"fields": {
                "failed_wf": "={{ $trigger.workflow_id }}",
                "failed_node": "={{ $trigger.failed_node }}",
                "error": "={{ $trigger.error }}",
            }}},
        ],
        "edges": [{"id": "e1", "source": "err", "target": "cap"}],
    }
    handler = client.post(
        "/api/v1/graph-workflows", json={"name": "alert handler", "graph": handler_graph},
        headers=auth_headers,
    ).json()
    client.post(f"/api/v1/graph-workflows/{handler['id']}/activate", headers=auth_headers)
    resp = client.post(
        f"/api/v1/graph-workflows/{handler['id']}/triggers",
        json={"type": "error", "config": {}}, headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text

    # A second handler watching a different workflow id must NOT fire.
    other = client.post(
        "/api/v1/graph-workflows", json={"name": "other handler", "graph": handler_graph},
        headers=auth_headers,
    ).json()
    client.post(f"/api/v1/graph-workflows/{other['id']}/activate", headers=auth_headers)
    client.post(
        f"/api/v1/graph-workflows/{other['id']}/triggers",
        json={"type": "error", "config": {"workflow_id": "someone-else"}}, headers=auth_headers,
    )

    failing_graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "boom", "type": "http.request", "params": {"url": "not-a-url"}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "boom"}],
    }
    failing = client.post(
        "/api/v1/graph-workflows", json={"name": "doomed flow", "graph": failing_graph},
        headers=auth_headers,
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{failing['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    before = len(captured_spawns)
    _drive_run(captured_spawns, run_id)

    # Exactly one handler run spawned (the catch-all; the filtered one stayed quiet).
    new_spawns = captured_spawns[before + 0:]
    handler_spawns = [s for s in new_spawns if s[0][0] != run_id and s[0][3] == "error"]
    assert len(handler_spawns) == 1
    handler_run_id = handler_spawns[0][0][0]
    asyncio.run(engine._execute(*handler_spawns[0][0], **handler_spawns[0][1]))

    handler_run = client.get(
        f"/api/v1/graph-workflows/runs/{handler_run_id}", headers=auth_headers
    ).json()
    assert handler_run["status"] == "completed", handler_run
    assert handler_run["workflow_id"] == handler["id"]
    assert handler_run["trigger_type"] == "error"
    by_node = {nr["node_id"]: nr for nr in handler_run["node_runs"]}
    cap = by_node["cap"]["output"]
    assert cap["failed_wf"] == failing["id"]
    assert cap["failed_node"] == "boom"
    assert "http.request" in cap["error"]

    runs_other = client.get(
        f"/api/v1/graph-workflows/{other['id']}/runs", headers=auth_headers
    ).json()
    assert runs_other == []  # the filtered handler never fired


# ── Phase 34 (roadmap fase 3): editor DX — node test + pinned outputs ────────

def test_single_node_test_executes_with_seeded_input(client, auth_headers, captured_spawns):
    # 3.1 — a full run persists outputs, then testing `b` alone re-executes only
    # it, resolving $node.a.output.x from history. No new run row is created.
    wf = _three_step_wf(client, auth_headers)
    client.post(f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers)
    _drive_last_run(captured_spawns)
    runs_before = client.get(f"/api/v1/graph-workflows/{wf['id']}/runs", headers=auth_headers).json()

    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/nodes/b/test", json={}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    res = resp.json()
    assert res["ok"] is True, res
    assert res["output"] == {"y": 20}
    assert res["handles"] == ["main"]
    assert res["duration_ms"] >= 0

    runs_after = client.get(f"/api/v1/graph-workflows/{wf['id']}/runs", headers=auth_headers).json()
    assert len(runs_after) == len(runs_before)  # tests never record runs


def test_node_test_accepts_unsaved_node_state_and_mock_input(client, auth_headers):
    # 3.1 — `node` carries the canvas' unsaved params; `input` mocks $json.
    wf = _three_step_wf(client, auth_headers)
    override = {"id": "b", "type": "set", "params": {"fields": {"z": "={{ $json.v + 1 }}"}}}
    res = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/nodes/b/test",
        json={"node": override, "input": {"v": 41}}, headers=auth_headers,
    ).json()
    assert res == {**res, "ok": True, "output": {"z": 42}}

    # id mismatch between path and body is rejected.
    bad = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/nodes/a/test",
        json={"node": override}, headers=auth_headers,
    )
    assert bad.status_code == 400


def test_node_test_failure_and_unknown_node(client, auth_headers):
    wf = _three_step_wf(client, auth_headers)
    # A failing node comes back as ok=False with the error inline (HTTP 200).
    res = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/nodes/b/test",
        json={"node": {"id": "b", "type": "http.request", "params": {"url": "not-a-url"}}},
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False and "http.request" in body["error"]
    # Unknown node id → 404.
    assert client.post(
        f"/api/v1/graph-workflows/{wf['id']}/nodes/nope/test", json={}, headers=auth_headers
    ).status_code == 404


def test_pinned_output_feeds_node_test_and_partial_run(client, auth_headers, captured_spawns):
    # 3.2 — pin `a`'s output: node tests and partial runs must use the pin
    # instead of run history (no full run ever executed here).
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "a", "type": "set", "params": {"fields": {"x": 10}}, "pinnedOutput": {"x": 100}},
            {"id": "b", "type": "set", "params": {"fields": {"y": "={{ $node.a.output.x * 2 }}"}}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "a"},
            {"id": "e2", "source": "a", "target": "b"},
        ],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "pinned flow", "graph": graph}, headers=auth_headers
    ).json()
    assert wf["graph"]["nodes"][1]["pinnedOutput"] == {"x": 100}  # persisted with the graph

    res = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/nodes/b/test", json={}, headers=auth_headers
    ).json()
    assert res["ok"] is True and res["output"] == {"y": 200}, res

    # Partial run from b: seeded from the pin as well.
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run",
        json={"payload": {}, "start_node_id": "b"}, headers=auth_headers,
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    b = next(nr for nr in run["node_runs"] if nr["node_id"] == "b")
    assert b["output"] == {"y": 200}

    # Expression preview honours the pin too.
    prev = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/preview-expression",
        json={"expression": "={{ $node.a.output.x }}"}, headers=auth_headers,
    ).json()
    assert prev == {"ok": True, "value": 100}


def test_pinned_output_is_ignored_by_production_runs(client, auth_headers, captured_spawns):
    # 3.2 — a full (manual/schedule/webhook/event) run executes the real node:
    # the pin never leaks into production data.
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "a", "type": "set", "params": {"fields": {"x": 10}}, "pinnedOutput": {"x": 100}},
            {"id": "b", "type": "set", "params": {"fields": {"y": "={{ $node.a.output.x * 2 }}"}}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "a"},
            {"id": "e2", "source": "a", "target": "b"},
        ],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "pinned prod flow", "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers
    ).json()["run_id"]
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["a"]["output"] == {"x": 10}   # the real node ran
    assert by_node["b"]["output"] == {"y": 20}   # downstream saw the real output


def test_loop_nodes_are_not_testable_in_isolation(client, auth_headers):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "loop", "type": "for", "params": {"items": [1, 2]}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "loop"}],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "loop test flow", "graph": graph}, headers=auth_headers
    ).json()
    res = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/nodes/loop/test", json={}, headers=auth_headers
    ).json()
    assert res["ok"] is False and "for/repeat" in res["error"]
