"""
Phase 49 — roadmap fase 17: scheduling, SLA and scale UX.

Covers per-schedule timezone + holiday/blackout windows (17.1), SLA monitors for
run overruns and missed schedule beats (17.2), the workflow navigator —
folders/tags/full-text search/archive (17.3), two-run comparison (17.4) and the
notification digest (17.5). Engine sweeps are called directly (the scheduler loop
is not started in tests); CRUD/search/compare go through the API.
"""

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.db import graph_workflow_repository as repo
from app.services import notification_service
from app.services import workflow_graph_service as engine


def _make_wf(client, auth_headers, name, graph=None, **extra):
    resp = client.post(
        "/api/v1/graph-workflows",
        json={"name": name, "graph": graph or {"nodes": [{"id": "t", "type": "manual"}]}, **extra},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _patch(client, auth_headers, wf_id, body):
    resp = client.patch(f"/api/v1/graph-workflows/{wf_id}", json=body, headers=auth_headers)
    assert resp.status_code == 200, resp.text
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
    return asyncio.run(_call())["run_id"]


def _ts(y, mo, d, h, mi, tz=timezone.utc):
    return int(datetime(y, mo, d, h, mi, tzinfo=tz).timestamp())


# ── 17.1 calendars, windows and per-schedule timezone ───────────────────────

def test_blackout_window_blocks_and_skip_dates():
    utc = ZoneInfo("UTC")
    blackout = {"windows": [{"start": "09:00", "end": "17:00"}], "on_conflict": "skip"}
    # 10:00 UTC is inside the window → blocked.
    blocked, action = engine._schedule_blocked({}, blackout, _ts(2026, 7, 22, 10, 0), utc)
    assert blocked and action == "skip"
    # 20:00 UTC is outside the window → runs.
    blocked, _ = engine._schedule_blocked({}, blackout, _ts(2026, 7, 22, 20, 0), utc)
    assert not blocked
    # Holiday skip date blocks regardless of time.
    blocked, _ = engine._schedule_blocked(
        {"skip_dates": ["2026-07-22"]}, {}, _ts(2026, 7, 22, 20, 0), utc
    )
    assert blocked


def test_blackout_window_uses_per_schedule_timezone():
    utc = ZoneInfo("UTC")
    blackout = {"windows": [{"start": "20:00", "end": "23:00"}]}
    fire = _ts(2026, 7, 22, 2, 0)  # 02:00 UTC == 22:00 previous day in New York (EDT)
    blocked_utc, _ = engine._schedule_blocked({}, blackout, fire, utc)
    blocked_ny, _ = engine._schedule_blocked({"tz": "America/New_York"}, blackout, fire, utc)
    assert not blocked_utc
    assert blocked_ny


def test_overnight_window_wraps_midnight():
    utc = ZoneInfo("UTC")
    blackout = {"windows": [{"start": "22:00", "end": "06:00"}]}
    assert engine._schedule_blocked({}, blackout, _ts(2026, 7, 22, 23, 30), utc)[0]
    assert engine._schedule_blocked({}, blackout, _ts(2026, 7, 22, 3, 0), utc)[0]
    assert not engine._schedule_blocked({}, blackout, _ts(2026, 7, 22, 12, 0), utc)[0]


# ── settings round-trip ─────────────────────────────────────────────────────

def test_workflow_settings_roundtrip(client, auth_headers):
    wf = _make_wf(client, auth_headers, "settings-wf")
    updated = _patch(client, auth_headers, wf["id"], {
        "blackout": {"windows": [{"start": "01:00", "end": "05:00"}], "on_conflict": "defer"},
        "sla": {"max_duration_s": 30, "missed_grace_s": 60, "channels": ["inapp"]},
        "notify": {"digest": {"enabled": True, "interval_s": 3600, "channel": "inapp"}},
        "folder": "prod/etl",
        "tags": ["nightly", "critical"],
    })
    assert updated["blackout"]["on_conflict"] == "defer"
    assert updated["sla"]["max_duration_s"] == 30
    assert updated["notify"]["digest"]["enabled"] is True
    assert updated["folder"] == "prod/etl"
    assert set(updated["tags"]) == {"nightly", "critical"}
    assert updated["archived"] is False


# ── 17.2 SLA monitors ───────────────────────────────────────────────────────

@pytest.fixture
def capture_alerts(monkeypatch):
    calls = []

    async def _fake_web(db, profile_id, event_type, title, body="", meta=None):
        calls.append(("inapp", profile_id, title, body))

    async def _fake_tg(db, profile_id, event_type, text, parse_mode=None, buttons=None):
        calls.append(("telegram", profile_id, text, ""))

    monkeypatch.setattr(notification_service, "notify_web", _fake_web)
    monkeypatch.setattr(notification_service, "notify_telegram", _fake_tg)
    return calls


def test_sla_duration_alert(client, auth_headers, capture_alerts):
    wf = _make_wf(client, auth_headers, "slow-wf")
    _patch(client, auth_headers, wf["id"], {"sla": {"max_duration_s": 10, "channels": ["inapp"]}})
    run_id = _run_sync(wf)

    async def _age_run():
        db = await engine._connect()
        try:
            # Backdate created_at so the recorded elapsed exceeds the 10s threshold.
            await db.execute(
                "UPDATE workflow_runs SET created_at = updated_at - 1000 WHERE id = ?", (run_id,)
            )
            await db.commit()
        finally:
            await db.close()
    asyncio.run(_age_run())

    asyncio.run(engine.check_sla_monitors())
    assert any("troppo lenta" in c[2] for c in capture_alerts)

    # Idempotent: a second sweep does not re-alert (run marked).
    capture_alerts.clear()
    asyncio.run(engine.check_sla_monitors())
    assert capture_alerts == []


def test_sla_missed_beat_alert(client, auth_headers, capture_alerts):
    wf = _make_wf(client, auth_headers, "missed-wf")
    _patch(client, auth_headers, wf["id"], {"sla": {"missed_grace_s": 30, "channels": ["inapp"]}})

    async def _setup_and_sweep():
        db = await engine._connect()
        try:
            tr = await repo.create_trigger(
                db, wf["id"], "schedule", {"recurrence": "daily"}, next_run_at=1_000_000
            )
            # next_run_at far in the past → overdue past the 30s grace.
            import time as _t
            await repo.set_trigger_next_run(db, tr.id, int(_t.time()) - 10_000)
        finally:
            await db.close()
        await engine.check_sla_monitors()
    asyncio.run(_setup_and_sweep())
    assert any("schedule mancato" in c[2] for c in capture_alerts)

    capture_alerts.clear()
    asyncio.run(engine.check_sla_monitors())
    assert capture_alerts == []  # deduped by last_sla_alert_at


# ── 17.3 navigator: folders, tags, search, archive ──────────────────────────

def test_search_by_name_tag_folder_and_archive(client, auth_headers):
    a = _make_wf(client, auth_headers, "Invoice Sync",
                 graph={"nodes": [{"id": "n", "type": "slack.post", "params": {}}]})
    b = _make_wf(client, auth_headers, "Nightly Report")
    _patch(client, auth_headers, a["id"], {"folder": "finance", "tags": ["billing"]})
    _patch(client, auth_headers, b["id"], {"folder": "ops", "archived": True})

    # Full-text over the name.
    hits = client.get("/api/v1/graph-workflows/search?q=invoice", headers=auth_headers).json()
    assert [w["id"] for w in hits] == [a["id"]]

    # Full-text reaches node contents ("slack" appears only in a's graph).
    hits = client.get("/api/v1/graph-workflows/search?q=slack", headers=auth_headers).json()
    assert a["id"] in [w["id"] for w in hits]

    # Tag filter.
    hits = client.get("/api/v1/graph-workflows/search?tag=billing", headers=auth_headers).json()
    assert [w["id"] for w in hits] == [a["id"]]

    # Folder filter.
    hits = client.get("/api/v1/graph-workflows/search?folder=finance", headers=auth_headers).json()
    assert [w["id"] for w in hits] == [a["id"]]

    # Archived hidden by default, shown when asked.
    hits = client.get("/api/v1/graph-workflows/search", headers=auth_headers).json()
    assert b["id"] not in [w["id"] for w in hits]
    hits = client.get("/api/v1/graph-workflows/search?include_archived=true", headers=auth_headers).json()
    assert b["id"] in [w["id"] for w in hits]

    # Folder tree.
    folders = client.get("/api/v1/graph-workflows/folders", headers=auth_headers).json()
    assert "finance" in folders and "ops" in folders


# ── 17.4 run comparison ─────────────────────────────────────────────────────

def test_compare_runs(client, auth_headers):
    # Two nodes: a manual trigger and a set node whose output depends on $trigger.
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "c", "type": "set", "params": {"fields": {"msg": "constant"}}},
            {"id": "s", "type": "set", "params": {"fields": {"v": "={{ $trigger.v }}"}}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "c"},
            {"id": "e2", "source": "c", "target": "s"},
        ],
    }
    wf = _make_wf(client, auth_headers, "compare-wf", graph=graph)
    run_a = _run_sync(wf, {"v": 1})
    run_b = _run_sync(wf, {"v": 2})

    resp = client.get(f"/api/v1/graph-workflows/runs/compare?a={run_a}&b={run_b}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_a"] == run_a and body["run_b"] == run_b
    assert body["status_a"] == "completed" and body["status_b"] == "completed"
    by_id = {n["node_id"]: n for n in body["nodes"]}
    # The constant node is identical across runs; the payload-dependent one diverges.
    assert by_id["c"]["output_equal"] is True
    assert by_id["s"]["output_equal"] is False
    assert by_id["s"]["output_a"] != by_id["s"]["output_b"]
    # First divergence is at the payload-carrying node (the manual trigger echoes
    # $trigger, or failing that the 'set' node that reads it).
    assert body["first_divergent_node"] in ("t", "s")


def test_compare_runs_rejects_cross_workflow(client, auth_headers):
    wf1 = _make_wf(client, auth_headers, "cw-1")
    wf2 = _make_wf(client, auth_headers, "cw-2")
    r1 = _run_sync(wf1)
    r2 = _run_sync(wf2)
    resp = client.get(f"/api/v1/graph-workflows/runs/compare?a={r1}&b={r2}", headers=auth_headers)
    assert resp.status_code == 400


# ── 17.5 notification digest ────────────────────────────────────────────────

def test_digest_buffers_then_flushes(client, auth_headers, capture_alerts):
    wf = _make_wf(client, auth_headers, "digest-wf")
    # interval_s=0 → the bucket is immediately flushable.
    _patch(client, auth_headers, wf["id"], {
        "notify": {"digest": {"enabled": True, "interval_s": 0, "channel": "inapp"}}
    })
    _run_sync(wf)
    _run_sync(wf)

    # Two runs buffered, nothing sent yet.
    async def _count():
        db = await engine._connect()
        try:
            return await repo.digest_outcome_counts(db, wf["id"], "inapp")
        finally:
            await db.close()
    assert asyncio.run(_count()).get("completed") == 2
    assert capture_alerts == []

    asyncio.run(engine.flush_notification_digests())
    # One aggregated summary delivered, buffer cleared.
    assert len(capture_alerts) == 1
    assert "Riepilogo" in capture_alerts[0][2]
    assert asyncio.run(_count()) == {}


def test_digest_disabled_is_noop(client, auth_headers, capture_alerts):
    wf = _make_wf(client, auth_headers, "no-digest-wf")
    _run_sync(wf)
    async def _count():
        db = await engine._connect()
        try:
            return await repo.digest_outcome_counts(db, wf["id"], "inapp")
        finally:
            await db.close()
    assert asyncio.run(_count()) == {}  # nothing buffered when digest is off
