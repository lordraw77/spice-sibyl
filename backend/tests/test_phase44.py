"""
Phase 44 — roadmap fase 12: data and budget governance.

Covers monthly LLM-token/run budgets and quotas (12.1 — workflow-level and
profile-wide "workspace" caps, hard stop + soft warning) and run log retention
and output redaction (12.2 — per-workflow TTL purge, masked node output paths
that stay cleartext for downstream expressions during the run).
"""

import asyncio
import json
import time

import pytest

from app.core.config import settings
from app.db import graph_workflow_repository as repo
from app.services import workflow_graph_service as engine

_MANUAL_GRAPH = {
    "nodes": [{"id": "t", "type": "manual"}],
    "edges": [],
}


@pytest.fixture()
def captured_spawns(monkeypatch):
    """The TestClient runs each request on a short-lived loop that cancels
    fire-and-forget tasks, so intercept ``_spawn`` and let the test drive
    ``_execute`` itself (mirrors the Phase 29 fixture of the same name)."""
    spawns: list[tuple] = []
    monkeypatch.setattr(engine, "_spawn", lambda *args, **kwargs: spawns.append((args, kwargs)))
    return spawns


def _drive_last_run(spawns):
    args, kwargs = spawns[-1]
    asyncio.run(engine._execute(*args, **kwargs))


def _make(client, auth_headers, graph=None, name="phase44 wf", **extra):
    body = {"name": name, "graph": graph or _MANUAL_GRAPH, **extra}
    resp = client.post("/api/v1/graph-workflows", json=body, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_run(wf, status="completed", tokens_total=None):
    async def _seed():
        db = await engine._connect()
        try:
            graph_json = json.dumps(_MANUAL_GRAPH)
            run_id = await repo.create_run(db, wf["id"], wf["profile_id"], "manual", graph_json)
            await repo.set_run_status(db, run_id, status)
            if tokens_total is not None:
                nr_id = await repo.start_node_run(db, run_id, "llm", "llm.completion", {})
                await repo.finish_node_run(
                    db, nr_id, "ok", output={"text": "hi", "_usage": {"tokens_total": tokens_total}},
                )
            return run_id
        finally:
            await db.close()

    return asyncio.run(_seed())


# ── fase 12.1: budgets and quotas ────────────────────────────────────────────

def test_workflow_run_budget_hard_stop(client, auth_headers):
    wf = _make(client, auth_headers, name="run cap wf", run_budget_month=1)
    _seed_run(wf)  # one run already this period
    resp = client.post(f"/api/v1/graph-workflows/{wf['id']}/run", json={}, headers=auth_headers)
    assert resp.status_code == 400
    assert "budget" in resp.json()["detail"].lower()


def test_workflow_token_budget_hard_stop(client, auth_headers):
    wf = _make(client, auth_headers, name="token cap wf", token_budget_month=100)
    _seed_run(wf, tokens_total=150)
    resp = client.post(f"/api/v1/graph-workflows/{wf['id']}/run", json={}, headers=auth_headers)
    assert resp.status_code == 400
    assert "budget" in resp.json()["detail"].lower()


def test_no_budget_configured_runs_normally(client, auth_headers, captured_spawns):
    wf = _make(client, auth_headers, name="unlimited wf")
    resp = client.post(f"/api/v1/graph-workflows/{wf['id']}/run", json={}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{resp.json()['run_id']}", headers=auth_headers).json()
    assert run["status"] == "completed"


def test_profile_budget_hard_stop(client, auth_headers):
    wf1 = _make(client, auth_headers, name="wf1")
    wf2 = _make(client, auth_headers, name="wf2")
    _seed_run(wf1)  # counts against the shared profile-wide cap
    resp = client.put(
        "/api/v1/graph-workflows/budget", json={"run_budget_month": 1}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["run_budget_month"] == 1

    resp = client.post(f"/api/v1/graph-workflows/{wf2['id']}/run", json={}, headers=auth_headers)
    assert resp.status_code == 400
    assert "profile-wide" in resp.json()["detail"]


def test_workflow_budget_status_endpoint(client, auth_headers):
    wf = _make(client, auth_headers, name="status wf", token_budget_month=1000, run_budget_month=10)
    _seed_run(wf, tokens_total=150)
    body = client.get(f"/api/v1/graph-workflows/{wf['id']}/budget", headers=auth_headers).json()
    assert body["token_budget_month"] == 1000
    assert body["run_budget_month"] == 10
    assert body["runs_used"] == 1
    assert body["tokens_used"] == 150
    assert body["exceeded"] is False
    assert "period" in body and len(body["period"]) == 7  # "YYYY-MM"


def test_get_profile_budget_defaults_to_unlimited(client, auth_headers):
    # The profile is shared across the whole test session (single sqlite
    # file) — reset explicitly first rather than assuming a pristine state.
    client.put(
        "/api/v1/graph-workflows/budget",
        json={"token_budget_month": None, "run_budget_month": None},
        headers=auth_headers,
    )
    body = client.get("/api/v1/graph-workflows/budget", headers=auth_headers).json()
    assert body["token_budget_month"] is None
    assert body["run_budget_month"] is None


def test_partial_run_bypasses_budget(client, auth_headers, captured_spawns):
    """A dev partial run (start_node_id) never counts against — or is gated
    by — a budget: it is an editor action, not production traffic."""
    wf = _make(client, auth_headers, name="partial wf", run_budget_month=1)
    _seed_run(wf)
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run",
        json={"start_node_id": "t"}, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


# ── fase 12.2: redaction ─────────────────────────────────────────────────────

_REDACT_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {
            "id": "secret", "type": "set",
            "params": {"fields": {"value": "={{ 'super-secret' }}", "public": "={{ 'ok' }}"}},
            "redact": ["value"],
        },
        {
            "id": "echo", "type": "set",
            "params": {"fields": {"copied": "={{ $node.secret.output.value }}"}},
        },
    ],
    "edges": [
        {"id": "e1", "source": "t", "target": "secret"},
        {"id": "e2", "source": "secret", "target": "echo"},
    ],
}


def test_redact_masks_persisted_output_but_not_downstream(client, auth_headers, captured_spawns):
    wf = _make(client, auth_headers, graph=_REDACT_GRAPH, name="redact wf")
    resp = client.post(f"/api/v1/graph-workflows/{wf['id']}/run", json={}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    _drive_last_run(captured_spawns)

    run = client.get(f"/api/v1/graph-workflows/runs/{resp.json()['run_id']}", headers=auth_headers).json()
    assert run["status"] == "completed"
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    # The masked field never reaches persistence...
    assert by_node["secret"]["output"]["value"] == "***"
    assert by_node["secret"]["output"]["public"] == "ok"
    # ...but the downstream node still resolved the real value at run time.
    assert by_node["echo"]["output"]["copied"] == "super-secret"


def test_redact_travels_on_pinned_output_export(client, auth_headers):
    graph = json.loads(json.dumps(_REDACT_GRAPH))
    graph["nodes"][1]["pinnedOutput"] = {"value": "super-secret", "public": "ok"}
    wf = _make(client, auth_headers, graph=graph, name="redact export wf")
    export = client.get(f"/api/v1/graph-workflows/{wf['id']}/export", headers=auth_headers).json()
    secret_node = next(n for n in export["graph"]["nodes"] if n["id"] == "secret")
    assert secret_node["pinnedOutput"]["value"] == "***"
    assert secret_node["pinnedOutput"]["public"] == "ok"


# ── fase 12.2: run retention purge ───────────────────────────────────────────

def test_retention_purge_respects_workflow_override(client, auth_headers):
    wf_override = _make(client, auth_headers, name="retained wf", runs_retention_days=1)
    wf_default = _make(client, auth_headers, name="default wf")
    run_override = _seed_run(wf_override)
    run_default = _seed_run(wf_default)

    old_ts = int(time.time()) - 10 * 86400

    async def _backdate(run_id):
        db = await engine._connect()
        try:
            await db.execute("UPDATE workflow_runs SET created_at = ? WHERE id = ?", (old_ts, run_id))
            await db.commit()
        finally:
            await db.close()

    asyncio.run(_backdate(run_override))
    asyncio.run(_backdate(run_default))

    # Global default disabled (0): only the workflow with its own override purges.
    async def _purge(default_days):
        db = await engine._connect()
        try:
            return await repo.purge_old_runs(db, default_days, int(time.time()))
        finally:
            await db.close()

    removed = asyncio.run(_purge(0))
    assert removed == 1
    assert client.get(f"/api/v1/graph-workflows/runs/{run_override}", headers=auth_headers).status_code == 404
    assert client.get(f"/api/v1/graph-workflows/runs/{run_default}", headers=auth_headers).status_code == 200

    # Clean up the still-backdated default-retention run so it doesn't leak
    # into other tests sharing this session's sqlite file.
    asyncio.run(_purge(1))


def test_retention_purge_global_default_covers_all(client, auth_headers):
    wf = _make(client, auth_headers, name="no override wf")
    run_id = _seed_run(wf)

    async def _backdate():
        db = await engine._connect()
        try:
            await db.execute(
                "UPDATE workflow_runs SET created_at = ? WHERE id = ?",
                (int(time.time()) - 10 * 86400, run_id),
            )
            await db.commit()
        finally:
            await db.close()

    asyncio.run(_backdate())

    async def _purge():
        db = await engine._connect()
        try:
            return await repo.purge_old_runs(db, 3, int(time.time()))
        finally:
            await db.close()

    removed = asyncio.run(_purge())
    assert removed == 1
    assert client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).status_code == 404


def test_retention_never_purges_active_runs(client, auth_headers):
    wf = _make(client, auth_headers, name="active retention wf", runs_retention_days=1)
    run_id = _seed_run(wf, status="running")

    async def _backdate():
        db = await engine._connect()
        try:
            await db.execute(
                "UPDATE workflow_runs SET created_at = ? WHERE id = ?",
                (int(time.time()) - 10 * 86400, run_id),
            )
            await db.commit()
        finally:
            await db.close()

    asyncio.run(_backdate())

    async def _purge():
        db = await engine._connect()
        try:
            return await repo.purge_old_runs(db, 0, int(time.time()))
        finally:
            await db.close()

    removed = asyncio.run(_purge())
    assert removed == 0
    assert client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).status_code == 200
