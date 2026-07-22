"""
Phase 42 — roadmap fase 10: advanced human-in-the-loop.

Covers ``human.input`` (form collection via JSON Schema, fase 10.1) and
``wait.event`` (correlation-id event delivery, fase 10.2). Both generalise the
Phase 35 ``workflow_approvals`` row (kind: approval|input|event) and reuse its
``waiting`` state, poll loop and resume-after-restart machinery — see
test_phase35.py for the human.approval coverage this builds on.
"""

import asyncio
import json

import pytest

from app.db import graph_workflow_repository as repo
from app.services import workflow_graph_service as engine


@pytest.fixture()
def captured_spawns(monkeypatch):
    spawns: list[tuple] = []
    monkeypatch.setattr(engine, "_spawn", lambda *args, **kwargs: spawns.append((args, kwargs)))
    return spawns


def _drive_last_run(spawns):
    args, kwargs = spawns[-1]
    asyncio.run(engine._execute(*args, **kwargs))


def _make_and_run(client, auth_headers, graph, payload=None, name="phase42 flow"):
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": name, "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": payload or {}}, headers=auth_headers
    ).json()["run_id"]
    return wf, run_id


# ── catalog ──────────────────────────────────────────────────────────────────

def test_catalog_includes_phase42_nodes(client, auth_headers):
    catalog = {t["type"]: t for t in client.get(
        "/api/v1/graph-workflows/node-types", headers=auth_headers
    ).json()}
    assert {"human.input", "wait.event"} <= set(catalog)
    assert catalog["human.input"]["outputs"] == ["submitted", "timeout"]
    assert catalog["wait.event"]["outputs"] == ["main", "timeout"]


# ── fase 10.1: human.input ──────────────────────────────────────────────────

_INPUT_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "form", "type": "human.input",
         "params": {
             "title": "Missing info",
             "message": "please fill",
             "timeout": 60,
             "schema": {
                 "type": "object",
                 "required": ["amount"],
                 "properties": {"amount": {"type": "number"}},
             },
         }},
        {"id": "ok", "type": "set", "params": {"fields": {"amount": "={{ $node.form.output.data.amount }}"}}},
        {"id": "to", "type": "set", "params": {"fields": {"went": "timeout"}}},
    ],
    "edges": [
        {"id": "e1", "source": "t", "target": "form"},
        {"id": "e2", "source": "form", "target": "ok", "sourceHandle": "submitted"},
        {"id": "e3", "source": "form", "target": "to", "sourceHandle": "timeout"},
    ],
}


def _drive_and_submit(captured_spawns, run_id, *, data, kind="input"):
    """Run the graph while a side task settles the pending human.input/wait.event
    request, mirroring test_phase35._drive_and_decide for human.approval."""
    args, kwargs = captured_spawns[-1]

    async def _go():
        task = asyncio.ensure_future(engine._execute(*args, **kwargs))
        db = await engine._connect()
        try:
            request = None
            for _ in range(200):
                await asyncio.sleep(0.03)
                run = await repo.get_run(db, run_id)
                rows = await repo.list_approvals(db, run.profile_id, status="pending", run_id=run_id, kind=kind)
                if rows:
                    request = rows[0]
                    break
            assert request is not None, "the waiting request never appeared"
            assert (await repo.get_run_status(db, run_id)) in ("waiting", "running")
            status = "submitted" if kind == "input" else "delivered"
            await repo.decide_approval(db, request.id, status=status, decided_by="tester", data=data)
        finally:
            await db.close()
        await task

    asyncio.run(_go())


def test_human_input_submitted_branch(client, auth_headers, captured_spawns, monkeypatch):
    monkeypatch.setattr(engine, "_APPROVAL_POLL_SECONDS", 0.05)
    _, run_id = _make_and_run(client, auth_headers, _INPUT_GRAPH, name="input submit")
    _drive_and_submit(captured_spawns, run_id, data={"amount": 42})
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["form"]["output"]["data"] == {"amount": 42}
    assert by_node["ok"]["status"] == "ok"
    assert by_node["ok"]["output"]["amount"] == 42
    assert by_node["to"]["status"] == "skipped"


def test_human_input_timeout_routes_to_timeout_branch(client, auth_headers, captured_spawns, monkeypatch):
    monkeypatch.setattr(engine, "_APPROVAL_POLL_SECONDS", 0.05)
    graph = json.loads(json.dumps(_INPUT_GRAPH))
    graph["nodes"][1]["params"]["timeout"] = 1  # clamped minimum
    _, run_id = _make_and_run(client, auth_headers, graph, name="input timeout")
    _drive_last_run(captured_spawns)  # nobody submits → expires after ~1s
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["form"]["output"]["status"] == "expired"
    assert by_node["to"]["status"] == "ok"
    assert by_node["ok"]["status"] == "skipped"


def test_human_input_submit_endpoint_validates_schema(client, auth_headers):
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "input api", "graph": _INPUT_GRAPH}, headers=auth_headers,
    ).json()

    async def _seed():
        db = await engine._connect()
        try:
            run_id = await repo.create_run(db, wf["id"], wf["profile_id"], "manual", "{}")
            approval = await repo.create_approval(
                db, run_id, "form", wf["id"], wf["profile_id"],
                title="Missing info", message="please", timeout_at=None,
                kind="input", schema={"type": "object", "required": ["amount"],
                                       "properties": {"amount": {"type": "number"}}},
            )
            return approval.id
        finally:
            await db.close()

    approval_id = asyncio.run(_seed())

    # Missing the required field → 422, request stays pending.
    resp = client.post(
        f"/api/v1/graph-workflows/approvals/{approval_id}/submit",
        json={"data": {}}, headers=auth_headers,
    )
    assert resp.status_code == 422

    resp = client.post(
        f"/api/v1/graph-workflows/approvals/{approval_id}/submit",
        json={"data": {"amount": 7}, "comment": "here"}, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "submitted"
    assert body["data"] == {"amount": 7}
    assert body["comment"] == "here"

    # Already settled → 409.
    resp = client.post(
        f"/api/v1/graph-workflows/approvals/{approval_id}/submit",
        json={"data": {"amount": 1}}, headers=auth_headers,
    )
    assert resp.status_code == 409


def test_human_input_single_node_test_is_refused(client, auth_headers):
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "input test", "graph": _INPUT_GRAPH}, headers=auth_headers,
    ).json()
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/nodes/form/test", json={}, headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "test" in body["error"]


# ── fase 10.2: wait.event ────────────────────────────────────────────────────

_EVENT_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "wait", "type": "wait.event",
         "params": {"correlationId": "={{ $trigger.order_id }}", "timeout": 60}},
        {"id": "ok", "type": "set", "params": {"fields": {"paid": "={{ $node.wait.output.paid }}"}}},
        {"id": "to", "type": "set", "params": {"fields": {"went": "timeout"}}},
    ],
    "edges": [
        {"id": "e1", "source": "t", "target": "wait"},
        {"id": "e2", "source": "wait", "target": "ok", "sourceHandle": "main"},
        {"id": "e3", "source": "wait", "target": "to", "sourceHandle": "timeout"},
    ],
}


def test_wait_event_delivered_branch(client, auth_headers, captured_spawns, monkeypatch):
    monkeypatch.setattr(engine, "_APPROVAL_POLL_SECONDS", 0.05)
    _, run_id = _make_and_run(
        client, auth_headers, _EVENT_GRAPH, payload={"order_id": "ord-1"}, name="event delivered"
    )
    _drive_and_submit(captured_spawns, run_id, data={"paid": True}, kind="event")
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["wait"]["output"] == {"paid": True}
    assert by_node["ok"]["status"] == "ok"
    assert by_node["ok"]["output"]["paid"] is True
    assert by_node["to"]["status"] == "skipped"


def test_wait_event_timeout_routes_to_timeout_branch(client, auth_headers, captured_spawns, monkeypatch):
    monkeypatch.setattr(engine, "_APPROVAL_POLL_SECONDS", 0.05)
    graph = json.loads(json.dumps(_EVENT_GRAPH))
    graph["nodes"][1]["params"]["timeout"] = 1  # clamped minimum
    _, run_id = _make_and_run(client, auth_headers, graph, payload={"order_id": "ord-2"}, name="event timeout")
    _drive_last_run(captured_spawns)  # nobody delivers → expires after ~1s
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["to"]["status"] == "ok"
    assert by_node["ok"]["status"] == "skipped"


def test_wait_event_requires_correlation_id():
    with pytest.raises(ValueError, match="correlationId"):
        asyncio.run(engine._exec_wait_event(
            None, "default",
            engine.GraphNode(id="wait", type="wait.event"),
            {}, {"_run_id": "r1"},
        ))


def test_deliver_event_endpoint(client, auth_headers):
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "event api", "graph": _EVENT_GRAPH}, headers=auth_headers,
    ).json()

    async def _seed():
        db = await engine._connect()
        try:
            run_id = await repo.create_run(db, wf["id"], wf["profile_id"], "manual", "{}")
            approval = await repo.create_approval(
                db, run_id, "wait", wf["id"], wf["profile_id"],
                title="Waiting for event 'ord-3'", message="", timeout_at=None,
                kind="event", correlation_id="ord-3",
            )
            return approval.id
        finally:
            await db.close()

    asyncio.run(_seed())

    resp = client.post(
        "/api/v1/graph-workflows/events/ord-3", json={"payload": {"paid": True}}, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "delivered"
    assert body["data"] == {"paid": True}

    # Already settled (no more pending rows for this correlation id) → 404.
    resp = client.post(
        "/api/v1/graph-workflows/events/ord-3", json={"payload": {}}, headers=auth_headers,
    )
    assert resp.status_code == 404

    resp = client.post(
        "/api/v1/graph-workflows/events/no-such-correlation", json={"payload": {}}, headers=auth_headers,
    )
    assert resp.status_code == 404
