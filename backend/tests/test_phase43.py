"""
Phase 43 — roadmap fase 11: workflow quality and testing.

Covers workflow test suites (11.1 — saved fixture + assertions, external nodes
mocked via a pin for determinism), the full dry-run (11.2 — the whole graph
simulated, external-effect nodes mocked with a pin or a typed placeholder) and
the pre-run cost estimate (11.3 — static tokens/month projection from
historical LLM usage and the active schedule frequency).
"""

import asyncio
import json

import pytest

from app.db import graph_workflow_repository as repo
from app.services import workflow_graph_service as engine

_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "calc", "type": "set", "params": {"fields": {"doubled": "={{ $trigger.n * 2 }}"}}},
        {
            "id": "http", "type": "http.request",
            "params": {"url": "https://example.invalid/x", "method": "GET"},
            "pinnedOutput": {"status": 200, "body": {"ok": True}},
        },
    ],
    "edges": [
        {"id": "e1", "source": "t", "target": "calc"},
        {"id": "e2", "source": "calc", "target": "http"},
    ],
}


def _make(client, auth_headers, graph=None, name="phase43 wf"):
    return client.post(
        "/api/v1/graph-workflows", json={"name": name, "graph": graph or _GRAPH}, headers=auth_headers
    ).json()


# ── fase 11.1: test suites ──────────────────────────────────────────────────

def test_test_case_crud(client, auth_headers):
    wf = _make(client, auth_headers, name="crud wf")
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/test-cases",
        json={"name": "case 1", "trigger_payload": {"n": 5}, "assertions": [
            {"node_id": "calc", "type": "json_path", "path": "doubled", "expected": 10},
        ]},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    case = resp.json()
    assert case["name"] == "case 1"
    assert case["trigger_payload"] == {"n": 5}
    assert case["workflow_id"] == wf["id"]

    listed = client.get(f"/api/v1/graph-workflows/{wf['id']}/test-cases", headers=auth_headers).json()
    assert len(listed) == 1 and listed[0]["id"] == case["id"]

    resp = client.put(
        f"/api/v1/graph-workflows/{wf['id']}/test-cases/{case['id']}",
        json={"name": "case 1 renamed", "trigger_payload": {"n": 6}, "assertions": []},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "case 1 renamed"
    assert resp.json()["trigger_payload"] == {"n": 6}

    resp = client.delete(f"/api/v1/graph-workflows/{wf['id']}/test-cases/{case['id']}", headers=auth_headers)
    assert resp.status_code == 204
    assert client.get(f"/api/v1/graph-workflows/{wf['id']}/test-cases", headers=auth_headers).json() == []


def test_test_case_not_found_on_other_workflow(client, auth_headers):
    wf1 = _make(client, auth_headers, name="wf1")
    wf2 = _make(client, auth_headers, name="wf2")
    case = client.post(
        f"/api/v1/graph-workflows/{wf1['id']}/test-cases",
        json={"name": "c", "trigger_payload": {}, "assertions": []},
        headers=auth_headers,
    ).json()
    resp = client.put(
        f"/api/v1/graph-workflows/{wf2['id']}/test-cases/{case['id']}",
        json={"name": "c2", "trigger_payload": {}, "assertions": []},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_run_test_suite_all_assertion_types_pass(client, auth_headers):
    wf = _make(client, auth_headers, name="suite wf")
    client.post(
        f"/api/v1/graph-workflows/{wf['id']}/test-cases",
        json={
            "name": "all pass",
            "trigger_payload": {"n": 5},
            "assertions": [
                {"node_id": "calc", "type": "json_path", "path": "doubled", "expected": 10},
                {"node_id": "calc", "type": "contains", "expected": "doubled"},
                {"node_id": "calc", "type": "schema", "expected": {
                    "type": "object", "required": ["doubled"], "properties": {"doubled": {"type": "number"}},
                }},
                {"node_id": "http", "type": "equals", "expected": {"status": 200, "body": {"ok": True}}},
            ],
        },
        headers=auth_headers,
    )
    resp = client.post(f"/api/v1/graph-workflows/{wf['id']}/test-cases/run", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["passed"] == 1, body
    assert body["failed"] == 0
    result = body["results"][0]
    assert result["passed"] is True
    assert all(a["passed"] for a in result["assertions"])
    # The pinned http node never made a real network call — its output IS the pin.
    http_assertion = next(a for a in result["assertions"] if a["node_id"] == "http")
    assert http_assertion["actual"] == {"status": 200, "body": {"ok": True}}


def test_run_test_suite_reports_failing_assertion(client, auth_headers):
    wf = _make(client, auth_headers, name="suite fail wf")
    client.post(
        f"/api/v1/graph-workflows/{wf['id']}/test-cases",
        json={
            "name": "wrong expectation",
            "trigger_payload": {"n": 5},
            "assertions": [
                {"node_id": "calc", "type": "json_path", "path": "doubled", "expected": 999},
            ],
        },
        headers=auth_headers,
    )
    body = client.post(f"/api/v1/graph-workflows/{wf['id']}/test-cases/run", headers=auth_headers).json()
    assert body["passed"] == 0
    assert body["failed"] == 1
    result = body["results"][0]
    assert result["passed"] is False
    assert result["assertions"][0]["passed"] is False
    assert "999" in result["assertions"][0]["message"]


# ── fase 11.2: full dry-run ──────────────────────────────────────────────────

def test_dry_run_mocks_pinned_external_node(client, auth_headers):
    wf = _make(client, auth_headers, name="dry run pinned")
    resp = client.post(f"/api/v1/graph-workflows/{wf['id']}/dry-run", json={"payload": {"n": 3}}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert set(body["path"]) == {"t", "calc", "http"}
    assert body["node_outputs"]["calc"]["doubled"] == 6
    assert body["node_outputs"]["http"] == {"status": 200, "body": {"ok": True}}
    effects = {e["node_id"]: e for e in body["external_effects"]}
    assert effects["http"]["source"] == "pin"
    assert effects["http"]["node_type"] == "http.request"


def test_dry_run_mocks_unpinned_external_node_with_placeholder(client, auth_headers):
    graph = json.loads(json.dumps(_GRAPH))
    graph["nodes"][2]["pinnedOutput"] = None  # no pin on the http node this time
    wf = _make(client, auth_headers, graph=graph, name="dry run placeholder")
    resp = client.post(f"/api/v1/graph-workflows/{wf['id']}/dry-run", json={"payload": {"n": 4}}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["node_outputs"]["http"]["_mocked"] is True
    assert body["node_outputs"]["http"]["status"] == 200  # typed placeholder shape
    effects = {e["node_id"]: e for e in body["external_effects"]}
    assert effects["http"]["source"] == "placeholder"


def test_dry_run_never_performs_the_real_http_call(client, auth_headers, monkeypatch):
    """The http.request executor would raise trying to reach example.invalid;
    a dry-run must never invoke it at all."""
    async def _boom(*args, **kwargs):
        raise AssertionError("the real http.request executor must not run during a dry-run")

    monkeypatch.setattr(engine, "_exec_http_request", _boom)
    graph = json.loads(json.dumps(_GRAPH))
    graph["nodes"][2]["pinnedOutput"] = None
    wf = _make(client, auth_headers, graph=graph, name="dry run no real call")
    resp = client.post(f"/api/v1/graph-workflows/{wf['id']}/dry-run", json={"payload": {"n": 1}}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "completed"


# ── fase 11.3: cost estimate ─────────────────────────────────────────────────

def test_cost_estimate_no_llm_nodes(client, auth_headers):
    wf = _make(client, auth_headers, name="no llm wf")
    resp = client.get(f"/api/v1/graph-workflows/{wf['id']}/cost-estimate", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["llm_node_count"] == 0
    assert body["avg_tokens_per_run"] is None
    assert body["tokens_per_month_est"] is None
    assert "no LLM nodes" in body["basis"]


_LLM_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "llm", "type": "llm.completion", "params": {"prompt": "hi"}},
    ],
    "edges": [{"id": "e1", "source": "t", "target": "llm"}],
}


def test_cost_estimate_no_run_history(client, auth_headers):
    wf = _make(client, auth_headers, graph=_LLM_GRAPH, name="llm no history")
    body = client.get(f"/api/v1/graph-workflows/{wf['id']}/cost-estimate", headers=auth_headers).json()
    assert body["llm_node_count"] == 1
    assert body["avg_tokens_per_run"] is None
    assert "no run history" in body["basis"]
    assert body["runs_per_month_est"] is None


def test_cost_estimate_projects_from_history_and_schedule(client, auth_headers):
    wf = _make(client, auth_headers, graph=_LLM_GRAPH, name="llm with history")

    async def _seed():
        db = await engine._connect()
        try:
            graph_json = json.dumps(_LLM_GRAPH)
            run_id = await repo.create_run(db, wf["id"], wf["profile_id"], "manual", graph_json)
            await repo.set_run_status(db, run_id, "completed")
            nr_id = await repo.start_node_run(db, run_id, "llm", "llm.completion", {})
            await repo.finish_node_run(
                db, nr_id, "ok",
                output={"text": "hi", "_usage": {"tokens_in": 100, "tokens_out": 50, "tokens_total": 150}},
            )
            await repo.create_trigger(
                db, wf["id"], "schedule", {"recurrence": "daily"}, next_run_at=None, enabled=True,
            )
        finally:
            await db.close()

    asyncio.run(_seed())

    body = client.get(f"/api/v1/graph-workflows/{wf['id']}/cost-estimate", headers=auth_headers).json()
    assert body["avg_tokens_per_run"] == 150
    assert body["runs_per_month_est"] == pytest.approx(30, rel=0.05)
    assert body["tokens_per_month_est"] == pytest.approx(150 * 30, rel=0.05)
    assert "historical average over 1 run" in body["basis"]
