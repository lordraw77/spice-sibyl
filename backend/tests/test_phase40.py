"""
Phase 40 — roadmap fase 8: advanced editor.

Covers the version visual diff (8.1), canvas notes/frames that persist with the
graph but are ignored by the engine (8.2), and step-by-step debugging: a run
created ``paused`` that advances one node per ``step``, runs to the next
breakpoint on ``continue``, honours an input override and is cancelled by
``stop`` or the session timeout (8.3).
"""

import asyncio

import pytest

from app.services import workflow_graph_service as engine


@pytest.fixture(autouse=True)
def _reset_sse_appstatus():
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


def _make_wf(client, auth_headers, name, graph, **extra):
    resp = client.post(
        "/api/v1/graph-workflows", json={"name": name, "graph": graph, **extra},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _run(client, auth_headers, wf_id, body=None):
    resp = client.post(
        f"/api/v1/graph-workflows/{wf_id}/run", json=body or {"payload": {}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


def _get_run(client, auth_headers, run_id):
    return client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()


def _debug(client, auth_headers, run_id, command, **extra):
    resp = client.post(
        f"/api/v1/graph-workflows/runs/{run_id}/debug",
        json={"command": command, **extra}, headers=auth_headers,
    )
    return resp


# manual t → set a → set b (linear, deterministic order)
_LINEAR_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "a", "type": "set", "params": {"fields": {"step": "a"}}},
        {"id": "b", "type": "set", "params": {"fields": {"step": "b"}}},
    ],
    "edges": [
        {"id": "e1", "source": "t", "target": "a"},
        {"id": "e2", "source": "a", "target": "b"},
    ],
}


# ── fase 8.1: version diff ───────────────────────────────────────────────────

def test_version_diff_reports_added_removed_and_changed_nodes(client, auth_headers):
    wf = _make_wf(client, auth_headers, "diff flow", _LINEAR_GRAPH)  # v1
    v2 = {
        "nodes": [
            {"id": "t", "type": "manual"},
            # 'a' changed config (params), 'b' removed, 'c' added
            {"id": "a", "type": "set", "params": {"fields": {"step": "a2"}}},
            {"id": "c", "type": "set", "params": {"fields": {"step": "c"}}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "a"},
            {"id": "e3", "source": "a", "target": "c"},
        ],
    }
    resp = client.patch(f"/api/v1/graph-workflows/{wf['id']}", json={"graph": v2}, headers=auth_headers)
    assert resp.json()["version"] == 2

    diff = client.get(
        f"/api/v1/graph-workflows/{wf['id']}/versions/1/diff/2", headers=auth_headers
    )
    assert diff.status_code == 200, diff.text
    d = diff.json()
    assert d["added_nodes"] == ["c"]
    assert d["removed_nodes"] == ["b"]
    assert [c["id"] for c in d["changed_nodes"]] == ["a"]
    assert d["unchanged_nodes"] == ["t"]
    assert any("->c" in e for e in d["added_edges"])
    assert any("->b" in e for e in d["removed_edges"])


def test_version_diff_ignores_node_position(client, auth_headers):
    wf = _make_wf(client, auth_headers, "diff pos flow", _LINEAR_GRAPH)
    moved = {
        "nodes": [
            {"id": "t", "type": "manual", "position": {"x": 500, "y": 500}},
            {"id": "a", "type": "set", "params": {"fields": {"step": "a"}}},
            {"id": "b", "type": "set", "params": {"fields": {"step": "b"}}},
        ],
        "edges": _LINEAR_GRAPH["edges"],
    }
    client.patch(f"/api/v1/graph-workflows/{wf['id']}", json={"graph": moved}, headers=auth_headers)
    d = client.get(
        f"/api/v1/graph-workflows/{wf['id']}/versions/1/diff/2", headers=auth_headers
    ).json()
    # Moving a node is not a config change.
    assert d["changed_nodes"] == []
    assert set(d["unchanged_nodes"]) == {"t", "a", "b"}


def test_version_diff_unknown_version_404(client, auth_headers):
    wf = _make_wf(client, auth_headers, "diff 404 flow", _LINEAR_GRAPH)
    resp = client.get(
        f"/api/v1/graph-workflows/{wf['id']}/versions/1/diff/99", headers=auth_headers
    )
    assert resp.status_code == 404


# ── fase 8.2: notes and frames ───────────────────────────────────────────────

_NOTED_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "a", "type": "set", "params": {"fields": {"done": True}}},
    ],
    "edges": [{"id": "e1", "source": "t", "target": "a"}],
    "notes": [
        {"id": "n1", "kind": "note", "text": "explain me", "color": "#ffd",
         "position": {"x": 10, "y": 10}},
        {"id": "f1", "kind": "frame", "text": "Group", "position": {"x": 0, "y": 0},
         "size": {"width": 400, "height": 300}},
    ],
}


def test_notes_and_frames_persist_with_the_graph(client, auth_headers):
    wf = _make_wf(client, auth_headers, "noted flow", _NOTED_GRAPH)
    got = client.get(f"/api/v1/graph-workflows/{wf['id']}", headers=auth_headers).json()
    notes = {n["id"]: n for n in got["graph"]["notes"]}
    assert notes["n1"]["kind"] == "note" and notes["n1"]["text"] == "explain me"
    assert notes["f1"]["kind"] == "frame" and notes["f1"]["size"]["width"] == 400


def test_engine_ignores_notes(client, auth_headers, captured_spawns):
    wf = _make_wf(client, auth_headers, "noted run flow", _NOTED_GRAPH)
    run_id = _run(client, auth_headers, wf["id"])
    _drive_last_run(captured_spawns)
    run = _get_run(client, auth_headers, run_id)
    assert run["status"] == "completed"
    executed = {nr["node_id"] for nr in run["node_runs"]}
    assert executed == {"t", "a"}  # notes n1/f1 never ran


def test_notes_travel_with_export(client, auth_headers):
    wf = _make_wf(client, auth_headers, "noted export flow", _NOTED_GRAPH)
    export = client.get(f"/api/v1/graph-workflows/{wf['id']}/export", headers=auth_headers).json()
    assert {n["id"] for n in export["graph"]["notes"]} == {"n1", "f1"}


# ── fase 8.3: step debugging ─────────────────────────────────────────────────

def _start_debug(client, auth_headers, wf_id, **extra):
    resp = client.post(
        f"/api/v1/graph-workflows/{wf_id}/run",
        json={"payload": {}, "debug": True, **extra}, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


def test_debug_run_starts_paused_without_executing(client, auth_headers, captured_spawns):
    wf = _make_wf(client, auth_headers, "debug start flow", _LINEAR_GRAPH)
    run_id = _start_debug(client, auth_headers, wf["id"])
    assert captured_spawns == []  # a debug run is not spawned until the first step
    run = _get_run(client, auth_headers, run_id)
    assert run["status"] == "paused"
    assert run["node_runs"] == []


def test_debug_step_advances_one_node_at_a_time(client, auth_headers, captured_spawns):
    wf = _make_wf(client, auth_headers, "debug step flow", _LINEAR_GRAPH)
    run_id = _start_debug(client, auth_headers, wf["id"])

    assert _debug(client, auth_headers, run_id, "step").status_code == 200
    _drive_last_run(captured_spawns)
    run = _get_run(client, auth_headers, run_id)
    assert run["status"] == "paused"
    assert {nr["node_id"] for nr in run["node_runs"]} == {"t"}
    assert run["debug"]["pending_node"] == "a"

    assert _debug(client, auth_headers, run_id, "step").status_code == 200
    _drive_last_run(captured_spawns)
    run = _get_run(client, auth_headers, run_id)
    assert run["status"] == "paused"
    assert {nr["node_id"] for nr in run["node_runs"]} == {"t", "a"}
    assert run["debug"]["pending_node"] == "b"

    # The last step runs b and the run completes (nothing left to pause before).
    assert _debug(client, auth_headers, run_id, "step").status_code == 200
    _drive_last_run(captured_spawns)
    run = _get_run(client, auth_headers, run_id)
    assert run["status"] == "completed"
    assert {nr["node_id"] for nr in run["node_runs"]} == {"t", "a", "b"}


def test_debug_continue_runs_to_a_breakpoint(client, auth_headers, captured_spawns):
    wf = _make_wf(client, auth_headers, "debug bp flow", _LINEAR_GRAPH)
    run_id = _start_debug(client, auth_headers, wf["id"], breakpoints=["b"])

    # continue runs t and a, then pauses before the breakpoint node b.
    assert _debug(client, auth_headers, run_id, "continue").status_code == 200
    _drive_last_run(captured_spawns)
    run = _get_run(client, auth_headers, run_id)
    assert run["status"] == "paused"
    assert {nr["node_id"] for nr in run["node_runs"]} == {"t", "a"}
    assert run["debug"]["pending_node"] == "b"

    # continue again runs b to completion (no further breakpoint).
    assert _debug(client, auth_headers, run_id, "continue").status_code == 200
    _drive_last_run(captured_spawns)
    run = _get_run(client, auth_headers, run_id)
    assert run["status"] == "completed"


def test_debug_input_override_mocks_next_node(client, auth_headers, captured_spawns):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "a", "type": "set", "params": {"fields": {"echo": "={{ $json.x }}"}}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "a"}],
    }
    wf = _make_wf(client, auth_headers, "debug input flow", graph)
    run_id = _start_debug(client, auth_headers, wf["id"])

    _debug(client, auth_headers, run_id, "step")  # run t, pause before a
    _drive_last_run(captured_spawns)

    _debug(client, auth_headers, run_id, "step", input={"x": 99})  # a sees the override
    _drive_last_run(captured_spawns)
    out = next(nr for nr in _get_run(client, auth_headers, run_id)["node_runs"]
               if nr["node_id"] == "a")["output"]
    assert out == {"echo": 99}


def test_debug_stop_cancels_the_run(client, auth_headers, captured_spawns):
    wf = _make_wf(client, auth_headers, "debug stop flow", _LINEAR_GRAPH)
    run_id = _start_debug(client, auth_headers, wf["id"])
    _debug(client, auth_headers, run_id, "step")
    _drive_last_run(captured_spawns)

    assert _debug(client, auth_headers, run_id, "stop").status_code == 200
    run = _get_run(client, auth_headers, run_id)
    assert run["status"] == "cancelled"


def test_debug_rejected_for_non_paused_runs(client, auth_headers, captured_spawns):
    wf = _make_wf(client, auth_headers, "debug reject flow", _LINEAR_GRAPH)
    run_id = _run(client, auth_headers, wf["id"])  # ordinary run
    _drive_last_run(captured_spawns)
    resp = _debug(client, auth_headers, run_id, "step")
    assert resp.status_code == 409


def test_stale_paused_runs_are_reaped(client, auth_headers, captured_spawns, monkeypatch):
    wf = _make_wf(client, auth_headers, "debug ttl flow", _LINEAR_GRAPH)
    run_id = _start_debug(client, auth_headers, wf["id"])
    # A zero TTL makes every paused run stale immediately.
    monkeypatch.setattr(engine.settings, "graph_workflow_debug_max_pause", 0)
    # (re-enable with a tiny positive value so the sweep actually runs)
    monkeypatch.setattr(engine.settings, "graph_workflow_debug_max_pause", 1)

    async def _sweep():
        import time as _t
        # Force the run's updated_at into the past so it is older than the cutoff.
        db = await engine._connect()
        try:
            await db.execute(
                "UPDATE workflow_runs SET updated_at = ? WHERE id = ?",
                (int(_t.time()) - 10, run_id),
            )
            await db.commit()
        finally:
            await db.close()
        await engine.cancel_stale_debug_runs()

    asyncio.run(_sweep())
    assert _get_run(client, auth_headers, run_id)["status"] == "cancelled"
