"""
Phase 35 — roadmap fase 4: new nodes & capabilities.

Covers the structured LLM nodes (llm.classify / llm.extract, fase 4.1), the
db/file nodes with their workspace-storage sandbox (fase 4.2) and the
human-in-the-loop human.approval node + /approvals API (fase 4.4). Fase 4.3
(tool-registry palette) is asserted in test_phase29 (catalog tests).
"""

import asyncio
import json

import pytest

from app.db import graph_workflow_repository as repo
from app.services import workflow_graph_service as engine


@pytest.fixture()
def captured_spawns(monkeypatch):
    """Same dance as test_phase29: intercept ``_spawn`` and let the test drive
    ``_execute`` itself (the TestClient's per-request loop cancels tasks)."""
    spawns: list[tuple] = []
    monkeypatch.setattr(engine, "_spawn", lambda *args, **kwargs: spawns.append((args, kwargs)))
    return spawns


def _drive_last_run(spawns):
    args, kwargs = spawns[-1]
    asyncio.run(engine._execute(*args, **kwargs))


def _make_and_run(client, auth_headers, graph, payload=None, name="phase35 flow"):
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": name, "graph": graph}, headers=auth_headers
    ).json()
    run_id = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": payload or {}}, headers=auth_headers
    ).json()["run_id"]
    return wf, run_id


def _fake_llm(monkeypatch, content: str):
    """Stub the completion layer with a canned reply (no provider involved)."""
    async def _fake(request):
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        }, "miss"

    monkeypatch.setattr(engine, "_cached_complete", _fake)


# ── catalog (fase 4 nodes are in the palette) ───────────────────────────────

def test_catalog_includes_phase35_nodes(client, auth_headers):
    catalog = {t["type"]: t for t in client.get(
        "/api/v1/graph-workflows/node-types", headers=auth_headers
    ).json()}
    assert {"llm.classify", "llm.extract", "db.query", "file.read",
            "file.write", "file.parse", "human.approval"} <= set(catalog)
    assert catalog["human.approval"]["outputs"] == ["approved", "rejected"]
    assert catalog["llm.classify"]["category"] == "ai"
    assert catalog["db.query"]["category"] == "data"


# ── fase 4.1: structured LLM nodes ──────────────────────────────────────────

def test_parse_llm_json_tolerates_fences_and_prose():
    assert engine._parse_llm_json('{"a": 1}') == {"a": 1}
    assert engine._parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert engine._parse_llm_json('Here you go: {"a": [1, 2]} hope it helps') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        engine._parse_llm_json("no json here")
    with pytest.raises(ValueError):
        engine._parse_llm_json('{"broken": ')


def test_llm_classify_node_returns_valid_category(client, auth_headers, captured_spawns, monkeypatch):
    _fake_llm(monkeypatch, '{"category": "bug", "confidence": 0.92}')
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "c", "type": "llm.classify",
             "params": {"input": "the app crashes on save", "categories": ["billing", "bug", "question"]}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "c"}],
    }
    _, run_id = _make_and_run(client, auth_headers, graph)
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    out = {nr["node_id"]: nr for nr in run["node_runs"]}["c"]["output"]
    assert out["category"] == "bug"
    assert out["confidence"] == 0.92


def test_llm_classify_rejects_category_outside_the_list(client, auth_headers, captured_spawns, monkeypatch):
    _fake_llm(monkeypatch, '{"category": "spam", "confidence": 1}')
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "c", "type": "llm.classify",
             "params": {"input": "x", "categories": ["a", "b"]}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "c"}],
    }
    _, run_id = _make_and_run(client, auth_headers, graph)
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "failed"
    assert "not one of" in (run["error"] or "")


def test_llm_json_call_falls_back_to_next_model_on_invalid_json(
    client, auth_headers, captured_spawns, monkeypatch
):
    """A chain candidate that replies with non-JSON prose must not abort the
    whole call — the chain moves on to the next model, exactly as it already
    does for a provider/network failure."""
    replies = {"mock/bad-json": "sure thing, here's your answer: bug", "mock/good-json": '{"category": "bug", "confidence": 0.5}'}

    async def _fake(request):
        return {
            "choices": [{"message": {"content": replies[request.model]}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        }, "miss"

    monkeypatch.setattr(engine, "_cached_complete", _fake)

    resp = client.put(
        "/api/v1/admin/model-failover-chains",
        json={"chains": {"bad-json-chain": ["mock/bad-json", "mock/good-json"]}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "c", "type": "llm.classify",
             "params": {
                 "model": "mock/bad-json", "input": "x", "categories": ["bug", "question"],
                 "failover_chain": "bad-json-chain",
             }},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "c"}],
    }
    _, run_id = _make_and_run(client, auth_headers, graph)
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    out = {nr["node_id"]: nr for nr in run["node_runs"]}["c"]["output"]
    assert out["category"] == "bug"
    assert out["model"] == "mock/good-json"
    assert out["_failover"] == {"tried": ["mock/bad-json", "mock/good-json"], "used": "mock/good-json"}


def test_llm_extract_node_enforces_required_properties(client, auth_headers, captured_spawns, monkeypatch):
    schema = {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "number"}},
              "required": ["name"]}
    _fake_llm(monkeypatch, '{"name": "Ada Lovelace", "age": 36}')
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "x", "type": "llm.extract",
             "params": {"input": "Ada Lovelace, 36, mathematician", "schema": schema}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "x"}],
    }
    _, run_id = _make_and_run(client, auth_headers, graph)
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    out = {nr["node_id"]: nr for nr in run["node_runs"]}["x"]["output"]
    assert out["data"] == {"name": "Ada Lovelace", "age": 36}

    # A reply missing a required property fails the node (retry/onError apply).
    _fake_llm(monkeypatch, '{"age": 36}')
    _, run_id = _make_and_run(client, auth_headers, graph, name="extract missing")
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "failed"
    assert "missing required" in (run["error"] or "")


# ── fase 4.2: workspace-storage sandbox + db/file nodes ─────────────────────

def test_safe_workspace_path_blocks_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.settings, "graph_workflow_files_dir", str(tmp_path))
    inside = engine._safe_workspace_path("a/b.txt")
    assert str(inside).startswith(str(tmp_path))
    for bad in ("../evil.txt", "/etc/passwd", "a/../../evil"):
        with pytest.raises(ValueError):
            engine._safe_workspace_path(bad)
    with pytest.raises(ValueError):
        engine._safe_workspace_path("")


def test_file_write_read_roundtrip_json_and_csv(client, auth_headers, captured_spawns, tmp_path, monkeypatch):
    monkeypatch.setattr(engine.settings, "graph_workflow_files_dir", str(tmp_path))
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "w", "type": "file.write",
             "params": {"path": "out/data.json", "content": {"greeting": "hello", "n": 2}}},
            {"id": "r", "type": "file.read", "params": {"path": "out/data.json"}},
            {"id": "wc", "type": "file.write",
             "params": {"path": "log.csv", "format": "csv", "append": "true",
                        "content": [{"a": 1, "b": "x"}]}},
            {"id": "rc", "type": "file.read", "params": {"path": "log.csv"}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "w"},
            {"id": "e2", "source": "w", "target": "r"},
            {"id": "e3", "source": "r", "target": "wc"},
            {"id": "e4", "source": "wc", "target": "rc"},
        ],
    }
    _, run_id = _make_and_run(client, auth_headers, graph)
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["r"]["output"]["data"] == {"greeting": "hello", "n": 2}
    rows = by_node["rc"]["output"]["rows"]
    assert rows == [{"a": "1", "b": "x"}]  # csv reads back as strings
    assert (tmp_path / "out" / "data.json").is_file()


def test_file_read_path_traversal_fails_the_node(client, auth_headers, captured_spawns, tmp_path, monkeypatch):
    monkeypatch.setattr(engine.settings, "graph_workflow_files_dir", str(tmp_path))
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "r", "type": "file.read", "params": {"path": "../../etc/passwd"}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "r"}],
    }
    _, run_id = _make_and_run(client, auth_headers, graph)
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "failed"
    assert "escapes" in (run["error"] or "")


def test_file_parse_node_parses_inflight_csv(client, auth_headers, captured_spawns):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "p", "type": "file.parse",
             "params": {"content": "name,qty\nwidget,3\nbolt,7", "format": "csv"}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "p"}],
    }
    _, run_id = _make_and_run(client, auth_headers, graph)
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    out = {nr["node_id"]: nr for nr in run["node_runs"]}["p"]["output"]
    assert out["count"] == 2
    assert out["rows"][0] == {"name": "widget", "qty": "3"}


def test_db_query_sqlite_roundtrip(client, auth_headers, captured_spawns, tmp_path, monkeypatch):
    monkeypatch.setattr(engine.settings, "graph_workflow_files_dir", str(tmp_path))
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "ddl", "type": "db.query",
             "params": {"database": "app.db",
                        "query": "CREATE TABLE IF NOT EXISTS items (name TEXT, qty INTEGER)"}},
            {"id": "ins", "type": "db.query",
             "params": {"database": "app.db",
                        "query": "INSERT INTO items (name, qty) VALUES (?, ?)",
                        "params": ["widget", 3]}},
            {"id": "sel", "type": "db.query",
             "params": {"database": "app.db", "query": "SELECT name, qty FROM items"}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "ddl"},
            {"id": "e2", "source": "ddl", "target": "ins"},
            {"id": "e3", "source": "ins", "target": "sel"},
        ],
    }
    _, run_id = _make_and_run(client, auth_headers, graph)
    _drive_last_run(captured_spawns)
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["ins"]["output"]["rowcount"] == 1
    assert by_node["sel"]["output"]["rows"] == [{"name": "widget", "qty": 3}]


# ── fase 4.4: human.approval ────────────────────────────────────────────────

_APPROVAL_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "gate", "type": "human.approval",
         "params": {"title": "Ship it?", "message": "please decide", "timeout": 60}},
        {"id": "yes", "type": "set", "params": {"fields": {"went": "approved"}}},
        {"id": "no", "type": "set", "params": {"fields": {"went": "rejected"}}},
    ],
    "edges": [
        {"id": "e1", "source": "t", "target": "gate"},
        {"id": "e2", "source": "gate", "target": "yes", "sourceHandle": "approved"},
        {"id": "e3", "source": "gate", "target": "no", "sourceHandle": "rejected"},
    ],
}


def _drive_and_decide(captured_spawns, run_id, *, status):
    """Run the graph while a side task settles the pending approval request."""
    args, kwargs = captured_spawns[-1]

    async def _go():
        task = asyncio.ensure_future(engine._execute(*args, **kwargs))
        db = await engine._connect()
        try:
            approval = None
            for _ in range(200):
                await asyncio.sleep(0.03)
                run = await repo.get_run(db, run_id)
                rows = await repo.list_approvals(db, run.profile_id, status="pending", run_id=run_id)
                if rows:
                    approval = rows[0]
                    break
            assert approval is not None, "the approval request never appeared"
            assert (await repo.get_run_status(db, run_id)) in ("waiting", "running")
            await repo.decide_approval(db, approval.id, status=status, decided_by="tester", comment="lgtm")
        finally:
            await db.close()
        await task

    asyncio.run(_go())


def test_human_approval_approved_branch(client, auth_headers, captured_spawns, monkeypatch):
    monkeypatch.setattr(engine, "_APPROVAL_POLL_SECONDS", 0.05)
    _, run_id = _make_and_run(client, auth_headers, _APPROVAL_GRAPH, name="gate approve")
    _drive_and_decide(captured_spawns, run_id, status="approved")
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["gate"]["output"]["approved"] is True
    assert by_node["gate"]["output"]["comment"] == "lgtm"
    assert by_node["yes"]["status"] == "ok"
    assert by_node["no"]["status"] == "skipped"


def test_human_approval_rejected_branch(client, auth_headers, captured_spawns, monkeypatch):
    monkeypatch.setattr(engine, "_APPROVAL_POLL_SECONDS", 0.05)
    _, run_id = _make_and_run(client, auth_headers, _APPROVAL_GRAPH, name="gate reject")
    _drive_and_decide(captured_spawns, run_id, status="rejected")
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["gate"]["output"]["approved"] is False
    assert by_node["yes"]["status"] == "skipped"
    assert by_node["no"]["status"] == "ok"


def test_human_approval_timeout_routes_to_rejected(client, auth_headers, captured_spawns, monkeypatch):
    monkeypatch.setattr(engine, "_APPROVAL_POLL_SECONDS", 0.05)
    graph = json.loads(json.dumps(_APPROVAL_GRAPH))
    graph["nodes"][1]["params"]["timeout"] = 1  # clamped minimum
    _, run_id = _make_and_run(client, auth_headers, graph, name="gate timeout")
    _drive_last_run(captured_spawns)  # nobody decides → expires after ~1s
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed", run
    by_node = {nr["node_id"]: nr for nr in run["node_runs"]}
    assert by_node["gate"]["output"]["status"] == "expired"
    assert by_node["no"]["status"] == "ok"


def test_approval_decision_endpoint_and_conflicts(client, auth_headers):
    # Build the rows directly (no engine): a run row satisfies the FK, then the
    # API decides the request once and 409s on the second attempt.
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "gate api", "graph": _APPROVAL_GRAPH},
        headers=auth_headers,
    ).json()

    async def _seed():
        db = await engine._connect()
        try:
            run_id = await repo.create_run(db, wf["id"], wf["profile_id"], "manual", "{}")
            approval = await repo.create_approval(
                db, run_id, "gate", wf["id"], wf["profile_id"],
                title="Ship it?", message="please", timeout_at=None,
            )
            return approval.id
        finally:
            await db.close()

    approval_id = asyncio.run(_seed())

    pending = client.get("/api/v1/graph-workflows/approvals", headers=auth_headers).json()
    assert any(a["id"] == approval_id for a in pending)

    resp = client.post(
        f"/api/v1/graph-workflows/approvals/{approval_id}/decision",
        json={"approved": True, "comment": "ok"}, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"
    assert resp.json()["comment"] == "ok"

    # Already settled → 409; unknown id → 404.
    resp = client.post(
        f"/api/v1/graph-workflows/approvals/{approval_id}/decision",
        json={"approved": False}, headers=auth_headers,
    )
    assert resp.status_code == 409
    resp = client.post(
        "/api/v1/graph-workflows/approvals/nope/decision",
        json={"approved": True}, headers=auth_headers,
    )
    assert resp.status_code == 404


def test_cancel_waiting_run_settles_pending_approvals(client, auth_headers, captured_spawns, monkeypatch):
    monkeypatch.setattr(engine, "_APPROVAL_POLL_SECONDS", 0.05)
    _, run_id = _make_and_run(client, auth_headers, _APPROVAL_GRAPH, name="gate cancel")
    args, kwargs = captured_spawns[-1]

    async def _go():
        task = asyncio.ensure_future(engine._execute(*args, **kwargs))
        db = await engine._connect()
        try:
            for _ in range(200):
                await asyncio.sleep(0.03)
                if await repo.get_run_status(db, run_id) == "waiting":
                    break
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert await repo.get_run_status(db, run_id) == "cancelled"
            run = await repo.get_run(db, run_id)
            rows = await repo.list_approvals(db, run.profile_id, status="pending", run_id=run_id)
            assert rows == []
            cancelled = await repo.list_approvals(db, run.profile_id, status="cancelled", run_id=run_id)
            assert len(cancelled) == 1
        finally:
            await db.close()

    asyncio.run(_go())


def test_human_approval_single_node_test_is_refused(client, auth_headers):
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "gate test", "graph": _APPROVAL_GRAPH},
        headers=auth_headers,
    ).json()
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/nodes/gate/test", json={}, headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "test" in body["error"]
