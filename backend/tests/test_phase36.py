"""
Phase 36 — roadmap fase 5: platform & product.

Covers per-workflow metrics (fase 5.1: GET /stats, token totals from `_usage`),
export/import hardening (fase 5.2: secret references in the export, POST /import
with validation warnings, workspace sharing of workflows) and LLM-generated
workflow drafts (fase 5.3: POST /generate with normalization).
"""

import asyncio
import json

import pytest

from app.services import workflow_graph_service as engine


@pytest.fixture(autouse=True)
def _reset_sse_appstatus():
    """sse-starlette binds its should_exit event to the first event loop it sees;
    the TestClient spins a fresh loop per test, so reset it before each one."""
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None


@pytest.fixture()
def captured_spawns(monkeypatch):
    spawns: list[tuple] = []
    monkeypatch.setattr(engine, "_spawn", lambda *args, **kwargs: spawns.append((args, kwargs)))
    return spawns


def _drive_last_run(spawns):
    args, kwargs = spawns[-1]
    asyncio.run(engine._execute(*args, **kwargs))


def _fake_llm(monkeypatch, content: str):
    async def _fake(request):
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }, "miss"

    monkeypatch.setattr("app.workflow.nodes.llm._cached_complete", _fake)


# ── fase 5.1: metrics ────────────────────────────────────────────────────────

def test_stats_reports_runs_success_rate_and_tokens(client, auth_headers, captured_spawns, monkeypatch):
    _fake_llm(monkeypatch, "hello there")
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "llm", "type": "llm.completion", "params": {"model": "mock/x", "prompt": "hi"}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "llm"}],
    }
    wf = client.post("/api/v1/graph-workflows", json={"name": "stats flow", "graph": graph},
                     headers=auth_headers).json()
    client.post(f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers)
    _drive_last_run(captured_spawns)

    stats = client.get("/api/v1/graph-workflows/stats", headers=auth_headers).json()
    row = next(s for s in stats if s["workflow_id"] == wf["id"])
    assert row["runs"] == 1
    assert row["completed"] == 1 and row["failed"] == 0
    assert row["success_rate"] == 1.0
    assert row["tokens_in"] == 10
    assert row["tokens_out"] == 20
    assert row["tokens_total"] == 30
    assert row["last_run_at"] is not None


def test_stats_success_rate_counts_failures(client, auth_headers, captured_spawns):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            # An unlinked telegram profile fails the node → the run fails.
            {"id": "tg", "type": "notify.telegram", "params": {"text": "x"}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "tg"}],
    }
    wf = client.post("/api/v1/graph-workflows", json={"name": "failing flow", "graph": graph},
                     headers=auth_headers).json()
    client.post(f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers)
    _drive_last_run(captured_spawns)
    stats = client.get("/api/v1/graph-workflows/stats", headers=auth_headers).json()
    row = next(s for s in stats if s["workflow_id"] == wf["id"])
    assert row["failed"] == 1
    assert row["success_rate"] == 0.0


# ── fase 5.2: export / import / sharing ─────────────────────────────────────

_SECRET_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "api", "type": "http.request",
         "params": {"url": "https://api.example.com",
                    "headers": {"Authorization": "Bearer ={{ $secrets.API_TOKEN }}"}}},
    ],
    "edges": [{"id": "e1", "source": "t", "target": "api"}],
}


def test_export_lists_secret_references(client, auth_headers):
    wf = client.post("/api/v1/graph-workflows", json={"name": "secret flow", "graph": _SECRET_GRAPH},
                     headers=auth_headers).json()
    exported = client.get(f"/api/v1/graph-workflows/{wf['id']}/export", headers=auth_headers).json()
    assert exported["secrets"] == ["API_TOKEN"]


def test_import_endpoint_creates_with_warnings(client, auth_headers):
    graph = json.loads(json.dumps(_SECRET_GRAPH))
    graph["nodes"].append({"id": "ghost", "type": "tool.does_not_exist"})
    graph["edges"].append({"id": "e2", "source": "api", "target": "ghost"})
    resp = client.post(
        "/api/v1/graph-workflows/import",
        json={"name": "imported", "description": "d", "graph": graph},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["workflow"]["name"] == "imported"
    joined = " ".join(body["warnings"])
    assert "tool.does_not_exist" in joined
    assert "$secrets.API_TOKEN" in joined
    # Once the secret exists, the warning goes away.
    client.put("/api/v1/graph-workflows/secrets", json={"name": "API_TOKEN", "value": "x"},
               headers=auth_headers)
    resp = client.post("/api/v1/graph-workflows/import",
                       json={"name": "imported 2", "graph": _SECRET_GRAPH}, headers=auth_headers)
    assert resp.status_code == 201
    assert resp.json()["warnings"] == []


def test_import_rejects_oversized_graph(client, auth_headers, monkeypatch):
    monkeypatch.setattr(engine.settings, "graph_workflow_max_nodes", 2)
    graph = {"nodes": [{"id": f"n{i}", "type": "set"} for i in range(3)], "edges": []}
    resp = client.post("/api/v1/graph-workflows/import",
                       json={"name": "too big", "graph": graph}, headers=auth_headers)
    assert resp.status_code == 400
    assert "limit" in resp.json()["detail"]


def test_workspace_workflow_share_and_import_copy(client, auth_headers):
    ws = client.post("/api/v1/workspaces", headers=auth_headers, json={"name": "WF team"}).json()
    wf = client.post("/api/v1/graph-workflows", json={"name": "shared flow", "graph": _SECRET_GRAPH},
                     headers=auth_headers).json()

    shared = client.post(f"/api/v1/workspaces/{ws['id']}/workflows",
                         headers=auth_headers, json={"workflow_id": wf["id"]})
    assert shared.status_code == 201, shared.text
    rows = shared.json()
    assert rows[0]["workflow_id"] == wf["id"]
    assert rows[0]["node_count"] == 2

    listed = client.get(f"/api/v1/workspaces/{ws['id']}/workflows", headers=auth_headers).json()
    assert len(listed) == 1

    copy = client.post(f"/api/v1/workspaces/{ws['id']}/workflows/{wf['id']}/import",
                       headers=auth_headers)
    assert copy.status_code == 201, copy.text
    body = copy.json()
    assert body["workflow"]["name"] == "shared flow (shared)"
    assert body["workflow"]["id"] != wf["id"]
    assert any("$secrets.API_TOKEN" in w for w in body["warnings"]) or body["warnings"] == []

    resp = client.delete(f"/api/v1/workspaces/{ws['id']}/workflows/{wf['id']}", headers=auth_headers)
    assert resp.status_code == 204
    assert client.get(f"/api/v1/workspaces/{ws['id']}/workflows", headers=auth_headers).json() == []
    # No longer shared → the copy route 404s.
    resp = client.post(f"/api/v1/workspaces/{ws['id']}/workflows/{wf['id']}/import", headers=auth_headers)
    assert resp.status_code == 404


# ── fase 5.3: LLM-generated drafts ──────────────────────────────────────────

def test_generate_returns_normalized_draft(client, auth_headers, monkeypatch):
    generated = {
        "name": "Daily digest",
        "description": "Summarize and notify",
        "graph": {
            "nodes": [
                # No trigger, one unknown type, no positions.
                {"id": "sum", "type": "llm.completion", "params": {"prompt": "={{ $trigger.text }}"}},
                {"id": "ghost", "type": "tool.nope"},
                {"id": "bell", "type": "notify.inapp", "params": {"title": "Digest"}},
            ],
            "edges": [
                {"id": "e1", "source": "sum", "target": "bell"},
                {"id": "e2", "source": "ghost", "target": "bell"},
            ],
        },
    }
    _fake_llm(monkeypatch, json.dumps(generated))
    resp = client.post("/api/v1/graph-workflows/generate",
                       json={"prompt": "make me a daily digest"}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Daily digest"
    types = [n["type"] for n in body["graph"]["nodes"]]
    assert "manual" in types          # trigger prepended
    assert "tool.nope" not in types   # unknown type dropped
    ids = {n["id"] for n in body["graph"]["nodes"]}
    for e in body["graph"]["edges"]:
        assert e["source"] in ids and e["target"] in ids
    for n in body["graph"]["nodes"]:
        assert "x" in n["position"] and "y" in n["position"]
    joined = " ".join(body["warnings"])
    assert "tool.nope" in joined and "manual trigger" in joined


def test_generate_rejects_non_graph_reply(client, auth_headers, monkeypatch):
    _fake_llm(monkeypatch, '{"whatever": true}')
    resp = client.post("/api/v1/graph-workflows/generate",
                       json={"prompt": "do something"}, headers=auth_headers)
    assert resp.status_code == 422


def test_generate_passes_model_and_failover_chain(client, auth_headers, monkeypatch):
    seen: dict = {}

    async def _capture(db, profile_id, params, system, prompt):
        seen.update(params)
        return {"name": "x", "description": "", "graph": {
            "nodes": [{"id": "t", "type": "manual"}], "edges": []}}, {"model": params.get("model")}

    # generate_workflow (engine copilot) calls the engine's re-exported binding.
    monkeypatch.setattr(engine, "_llm_json_call", _capture)
    resp = client.post(
        "/api/v1/graph-workflows/generate",
        json={"prompt": "anything", "model": "mock/pick-me", "failover_chain": "my-chain"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert seen["model"] == "mock/pick-me"
    assert seen["failover_chain"] == "my-chain"


def test_generate_stream_emits_log_then_done(client, auth_headers, monkeypatch):
    generated = {
        "name": "Streamed",
        "description": "",
        "graph": {"nodes": [{"id": "t", "type": "manual"},
                            {"id": "s", "type": "set", "params": {"fields": {"a": 1}}}],
                  "edges": [{"id": "e1", "source": "t", "target": "s"}]},
    }
    _fake_llm(monkeypatch, json.dumps(generated))
    with client.stream(
        "POST", "/api/v1/graph-workflows/generate/stream",
        json={"prompt": "stream me a workflow"}, headers=auth_headers,
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    # Progress log events for each stage, then the draft on `done`.
    assert "event: log" in body
    for step in ("catalog", "calling", "received", "normalized", "layout"):
        assert f'"step": "{step}"' in body or f'"step":"{step}"' in body
    assert "event: done" in body
    assert "Streamed" in body


def test_generate_stream_surfaces_errors_as_event(client, auth_headers, monkeypatch):
    _fake_llm(monkeypatch, "not json at all")
    with client.stream(
        "POST", "/api/v1/graph-workflows/generate/stream",
        json={"prompt": "broken"}, headers=auth_headers,
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "event: error" in body
    assert "event: done" not in body
