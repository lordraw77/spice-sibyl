"""
Phase 48 — roadmap fase 16: state and execution semantics.

Covers persistent state across runs (16.1 — state.get/set/increment over the
per-workflow key/value store, with TTL and lazy expiry), trigger idempotency
(16.2 — dedupKey/dedupWindow returning the original run on a repeat delivery),
compensations / saga (16.3 — a failed run walks completed nodes in reverse and
runs their `compensate` branch), and run priority (16.4 — the per-workflow queue
serves higher priority first, FIFO within a priority). Full runs use
``run_workflow_sync`` so they execute inline and deterministically.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.db import graph_workflow_repository as repo
from app.services import workflow_graph_service as engine


def _make_wf(client, auth_headers, name, graph, **extra):
    resp = client.post(
        "/api/v1/graph-workflows", json={"name": name, "graph": graph, **extra},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _run_sync(wf, payload=None):
    async def _call():
        db = await engine._connect()
        try:
            return await engine.run_workflow_sync(
                db, wf["id"], wf["profile_id"], trigger_payload=payload or {}
            )
        finally:
            await db.close()
    return asyncio.run(_call())


def _state(wf, key):
    async def _call():
        db = await engine._connect()
        try:
            return await repo.state_get(db, wf["id"], key)
        finally:
            await db.close()
    return asyncio.run(_call())


# ── catalog ──────────────────────────────────────────────────────────────────

def test_catalog_includes_state_nodes(client, auth_headers):
    catalog = {t["type"]: t for t in client.get(
        "/api/v1/graph-workflows/node-types", headers=auth_headers
    ).json()}
    assert {"state.get", "state.set", "state.increment"} <= set(catalog)
    assert catalog["state.increment"]["outputs"] == ["main"]


# ── 16.1 persistent state across runs ───────────────────────────────────────

_INCREMENT_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "inc", "type": "state.increment", "params": {"key": "counter"}},
    ],
    "edges": [{"id": "e1", "source": "t", "target": "inc"}],
}


def test_state_increment_persists_across_runs(client, auth_headers):
    wf = _make_wf(client, auth_headers, "counter flow", _INCREMENT_GRAPH)
    r1 = _run_sync(wf)
    r2 = _run_sync(wf)
    r3 = _run_sync(wf)
    assert r1["status"] == "completed"
    assert r1["output"] == {"key": "counter", "value": 1}
    assert r2["output"]["value"] == 2
    assert r3["output"]["value"] == 3
    found, value = _state(wf, "counter")
    assert found and value == 3


def test_state_set_then_get_in_same_run(client, auth_headers):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "s", "type": "state.set", "params": {"key": "cursor", "value": "abc"}},
            {"id": "g", "type": "state.get", "params": {"key": "cursor"}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "s"},
            {"id": "e2", "source": "s", "target": "g"},
        ],
    }
    wf = _make_wf(client, auth_headers, "set/get flow", graph)
    result = _run_sync(wf)
    assert result["status"] == "completed"
    assert result["output"] == {"key": "cursor", "value": "abc", "found": True}


def test_state_get_default_when_missing(client, auth_headers):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "g", "type": "state.get", "params": {"key": "nope", "default": 42}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "g"}],
    }
    wf = _make_wf(client, auth_headers, "default flow", graph)
    result = _run_sync(wf)
    assert result["output"] == {"key": "nope", "value": 42, "found": False}


def test_state_ttl_lazy_expiry(client, auth_headers):
    wf = _make_wf(client, auth_headers, "ttl flow", _INCREMENT_GRAPH)
    async def _drive():
        db = await engine._connect()
        try:
            wf_id = wf["id"]
            await repo.state_set(db, wf_id, "temp", {"a": 1}, ttl_seconds=100)
            fresh = await repo.state_get(db, wf_id, "temp")
            # Read "in the future": the key has expired and reads as absent.
            expired = await repo.state_get(db, wf_id, "temp", now=2 ** 40)
            purged = await repo.purge_expired_state(db, now=2 ** 40)
            return fresh, expired, purged
        finally:
            await db.close()
    fresh, expired, purged = asyncio.run(_drive())
    assert fresh == (True, {"a": 1})
    assert expired == (False, None)
    assert purged >= 1


def test_state_api_roundtrip(client, auth_headers):
    wf = _make_wf(client, auth_headers, "state api", _INCREMENT_GRAPH)
    # Set by hand.
    put = client.put(
        f"/api/v1/graph-workflows/{wf['id']}/state/manual_key",
        json={"value": {"n": 7}}, headers=auth_headers,
    )
    assert put.status_code == 200, put.text
    assert put.json()["value"] == {"n": 7}
    # List shows it.
    listed = client.get(f"/api/v1/graph-workflows/{wf['id']}/state", headers=auth_headers).json()
    assert any(row["key"] == "manual_key" and row["value"] == {"n": 7} for row in listed)
    # Delete removes it.
    assert client.delete(
        f"/api/v1/graph-workflows/{wf['id']}/state/manual_key", headers=auth_headers
    ).status_code == 204
    listed = client.get(f"/api/v1/graph-workflows/{wf['id']}/state", headers=auth_headers).json()
    assert not any(row["key"] == "manual_key" for row in listed)


# ── 16.2 trigger idempotency ────────────────────────────────────────────────

def test_resolve_dedup_key_from_expression():
    key = asyncio.run(engine._resolve_dedup_key(
        {"dedupKey": "{{ $trigger.order_id }}"}, {"order_id": "ORD-1"}
    ))
    assert key == "ORD-1"
    # No dedupKey configured → None (dedup disabled).
    assert asyncio.run(engine._resolve_dedup_key({}, {"order_id": "x"})) is None
    # Key resolves to empty → None (never dedupe on a blank key).
    assert asyncio.run(engine._resolve_dedup_key(
        {"dedupKey": "{{ $trigger.missing }}"}, {}
    )) is None


def test_run_from_trigger_dedupes_repeat_delivery(client, auth_headers, monkeypatch):
    monkeypatch.setattr(engine, "_spawn", lambda *a, **k: None)
    wf_dict = _make_wf(client, auth_headers, "dedup flow", _INCREMENT_GRAPH)

    async def _drive():
        db = await engine._connect()
        try:
            wf = await repo.get_workflow(db, wf_dict["id"])
            trig = SimpleNamespace(
                id="trig-dedup",
                config={"dedupKey": "{{ $trigger.order_id }}", "dedupWindowSeconds": 300},
            )
            r1 = await engine.run_from_trigger(db, trig, wf, "webhook", {"order_id": "A"})
            r2 = await engine.run_from_trigger(db, trig, wf, "webhook", {"order_id": "A"})
            r3 = await engine.run_from_trigger(db, trig, wf, "webhook", {"order_id": "B"})
            return r1, r2, r3
        finally:
            await db.close()

    (r1, d1), (r2, d2), (r3, d3) = asyncio.run(_drive())
    assert d1 is False and r1
    assert d2 is True and r2 == r1     # repeat of A → original run, no new run
    assert d3 is False and r3 != r1    # different key → new run


def test_webhook_endpoint_dedupes(client, auth_headers, monkeypatch):
    monkeypatch.setattr(engine, "_spawn", lambda *a, **k: None)
    wf = _make_wf(client, auth_headers, "hook dedup", _INCREMENT_GRAPH)
    client.post(f"/api/v1/graph-workflows/{wf['id']}/activate", headers=auth_headers)
    trig = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "webhook", "config": {"dedupKey": "{{ $trigger.id }}", "dedupWindowSeconds": 300}},
        headers=auth_headers,
    ).json()
    token = trig["token"]
    a = client.post(f"/api/v1/wf/hooks/{token}", json={"id": "evt-1"})
    b = client.post(f"/api/v1/wf/hooks/{token}", json={"id": "evt-1"})
    assert a.status_code == 200 and b.status_code == 200
    assert a.json()["deduped"] is False
    assert b.json()["deduped"] is True
    assert b.json()["run_id"] == a.json()["run_id"]


# ── 16.3 compensations (saga) ───────────────────────────────────────────────

# t → book (state.set marks the side effect) → fail (empty key → raises).
# `book` wires a `compensate` edge to `undo` (state.set marks the rollback).
_SAGA_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "book", "type": "state.set", "params": {"key": "booked", "value": True}},
        {"id": "fail", "type": "state.get", "params": {"key": ""}},
        {"id": "undo", "type": "state.set", "params": {"key": "rolled_back", "value": True}},
    ],
    "edges": [
        {"id": "e1", "source": "t", "target": "book"},
        {"id": "e2", "source": "book", "target": "fail"},
        {"id": "e3", "source": "book", "target": "undo", "sourceHandle": "compensate"},
    ],
}


def test_compensation_runs_on_failure(client, auth_headers):
    wf = _make_wf(client, auth_headers, "saga flow", _SAGA_GRAPH)
    result = _run_sync(wf)
    assert result["status"] == "failed"
    # The forward side effect happened…
    assert _state(wf, "booked") == (True, True)
    # …and its compensation ran because the run failed downstream.
    assert _state(wf, "rolled_back") == (True, True)


def test_no_compensation_on_success(client, auth_headers):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "book", "type": "state.set", "params": {"key": "ok_booked", "value": True}},
            {"id": "undo", "type": "state.set", "params": {"key": "ok_rolled_back", "value": True}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "book"},
            {"id": "e2", "source": "book", "target": "undo", "sourceHandle": "compensate"},
        ],
    }
    wf = _make_wf(client, auth_headers, "happy saga", graph)
    result = _run_sync(wf)
    assert result["status"] == "completed"
    assert _state(wf, "ok_booked") == (True, True)
    # The compensate branch must NOT run when nothing failed.
    assert _state(wf, "ok_rolled_back") == (False, None)


# ── 16.4 run priority ───────────────────────────────────────────────────────

def test_next_queued_run_orders_by_priority_then_fifo(client, auth_headers):
    wf = _make_wf(client, auth_headers, "prio queue", _INCREMENT_GRAPH)
    async def _drive():
        db = await engine._connect()
        try:
            wf_id = wf["id"]
            graph_json = json.dumps({"nodes": [], "edges": []})
            low = await repo.create_run(db, wf_id, "default", "manual", graph_json, status="queued", priority=0)
            high = await repo.create_run(db, wf_id, "default", "manual", graph_json, status="queued", priority=10)
            mid1 = await repo.create_run(db, wf_id, "default", "manual", graph_json, status="queued", priority=5)
            mid2 = await repo.create_run(db, wf_id, "default", "manual", graph_json, status="queued", priority=5)
            order = []
            for _ in range(4):
                nxt = await repo.next_queued_run(db, wf_id)
                order.append(nxt.id)
                await repo.set_run_status(db, nxt.id, "completed")
            return order, {"low": low, "high": high, "mid1": mid1, "mid2": mid2}
        finally:
            await db.close()
    order, ids = asyncio.run(_drive())
    # Highest priority first; FIFO (creation order) within the same priority.
    assert order == [ids["high"], ids["mid1"], ids["mid2"], ids["low"]]


def test_run_priority_recorded_on_run_row(client, auth_headers):
    wf = _make_wf(client, auth_headers, "prio run", _INCREMENT_GRAPH)
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run",
        json={"payload": {}, "priority": 7}, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    async def _get():
        db = await engine._connect()
        try:
            return await repo.get_run(db, run_id)
        finally:
            await db.close()
    run = asyncio.run(_get())
    assert run.priority == 7
