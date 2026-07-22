"""
Phase 46 — roadmap fase 14: remote execution and scalability.

Covers remote runners (14.1 — registration/heartbeat/dispatch protocol, the
stateless subset dispatcher shared with the runner agent, timeout/fallback
semantics), confirms the `code` node already executes in the Phase 18
sandboxed subprocess so 14.2 needs no new isolation layer, the generic
per-run lease (14.3), the pluggable QueueDriver + `queue.publish`/
`queue.consume` (14.4) and the `sibyl-wf` CLI (14.5) against an in-process
ASGI transport (no real network).
"""

import asyncio
import json

import httpx
import pytest

from app.cli import sibyl_wf
from app.db import graph_workflow_repository as repo
from app.schemas.graph_workflows import GraphNode
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


def _make(client, auth_headers, graph, name="phase46 wf", **extra):
    resp = client.post(
        "/api/v1/graph-workflows", json={"name": name, "graph": graph, **extra}, headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


_SIMPLE_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "out", "type": "set", "params": {"fields": {"done": True}}},
    ],
    "edges": [{"id": "e1", "source": "t", "target": "out"}],
}


async def _seed_runner(profile_id="default", name="runner1", labels=None, allowed=None, heartbeat=True):
    db = await engine._connect()
    try:
        runner_id, token = await repo.create_runner(
            db, profile_id, name, labels or ["gpu"], allowed or [],
        )
        if heartbeat:
            await repo.heartbeat_runner(db, runner_id, version="1.0", labels=None)
        return runner_id, token
    finally:
        await db.close()


def _auto_complete_factory(monkeypatch):
    """Patches repo.create_runner_job so every job it creates is instantly
    'executed' by the real stateless dispatcher and marked done — stands in
    for a runner agent that is always immediately available, without needing
    real concurrency inside a single asyncio.run() drive."""
    real_create = repo.create_runner_job

    async def _auto_complete(db, runner_id, run_id, node_id, node_type, payload):
        job_id = await real_create(db, runner_id, run_id, node_id, node_type, payload)
        output, handles = await engine._dispatch_stateless(node_type, payload["params"], payload["input"])
        await repo.finish_runner_job(db, job_id, ok=True, result={"output": output, "handles": handles})
        return job_id

    monkeypatch.setattr(repo, "create_runner_job", _auto_complete)


# ── fase 14.1: remote runner registration/heartbeat/dispatch protocol ──────

def test_register_heartbeat_list_and_revoke_runner(client, auth_headers):
    resp = client.post(
        "/api/v1/graph-workflows/runners",
        json={"name": "gpu-box", "labels": ["gpu"], "allowed_node_types": ["code"]},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    runner_id, token = resp.json()["id"], resp.json()["token"]
    assert token

    resp = client.get("/api/v1/graph-workflows/runners", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json() if r["id"] == runner_id)
    assert row["status"] == "offline"

    resp = client.post(
        "/api/v1/wf/runners/heartbeat", json={"version": "1.2.3"},
        headers={"X-Runner-Token": token},
    )
    assert resp.status_code == 200, resp.text

    resp = client.get("/api/v1/graph-workflows/runners", headers=auth_headers)
    row = next(r for r in resp.json() if r["id"] == runner_id)
    assert row["status"] == "online"
    assert row["version"] == "1.2.3"

    resp = client.delete(f"/api/v1/graph-workflows/runners/{runner_id}", headers=auth_headers)
    assert resp.status_code == 204

    resp = client.post(
        "/api/v1/wf/runners/heartbeat", json={"version": "1.2.3"},
        headers={"X-Runner-Token": token},
    )
    assert resp.status_code == 401


def test_heartbeat_rejects_missing_or_invalid_token(client):
    resp = client.post("/api/v1/wf/runners/heartbeat", json={})
    assert resp.status_code == 401
    resp = client.post("/api/v1/wf/runners/heartbeat", json={}, headers={"X-Runner-Token": "bogus"})
    assert resp.status_code == 401


def test_poll_job_returns_204_when_nothing_queued(client):
    _, token = asyncio.run(_seed_runner())
    resp = client.get(
        "/api/v1/wf/runners/jobs/next", params={"wait": 0.2}, headers={"X-Runner-Token": token},
    )
    assert resp.status_code == 204


def test_poll_claims_a_queued_job_and_result_completes_it(client):
    runner_id, token = asyncio.run(_seed_runner())

    async def _seed_job():
        db = await engine._connect()
        try:
            return await repo.create_runner_job(
                db, runner_id, None, "n1", "set", {"params": {"fields": {"a": 1}}, "input": None},
            )
        finally:
            await db.close()

    job_id = asyncio.run(_seed_job())

    resp = client.get(
        "/api/v1/wf/runners/jobs/next", params={"wait": 1}, headers={"X-Runner-Token": token},
    )
    assert resp.status_code == 200, resp.text
    job = resp.json()
    assert job["job_id"] == job_id
    assert job["node_type"] == "set"
    assert job["params"] == {"fields": {"a": 1}}

    resp = client.post(
        f"/api/v1/wf/runners/jobs/{job_id}/result",
        json={"ok": True, "output": {"a": 1}, "handles": ["main"], "logs": []},
        headers={"X-Runner-Token": token},
    )
    assert resp.status_code == 200, resp.text

    # A second submit for the same (already-settled) job is rejected.
    resp = client.post(
        f"/api/v1/wf/runners/jobs/{job_id}/result",
        json={"ok": True, "output": {}, "handles": ["main"], "logs": []},
        headers={"X-Runner-Token": token},
    )
    assert resp.status_code == 409


def test_dispatch_stateless_covers_the_remote_capable_types():
    async def _t():
        out, handles = await engine._dispatch_stateless("set", {"fields": {"x": 1}}, None)
        assert out == {"x": 1} and handles == ["main"]
        out, handles = await engine._dispatch_stateless("if", {"condition": True}, "in")
        assert handles == ["true"]
        out, handles = await engine._dispatch_stateless("code", {"code": "print(input)"}, 42)
        assert "42" in out["stdout"]

    asyncio.run(_t())


def test_dispatch_remote_ignores_non_remote_capable_types():
    node = GraphNode(id="n1", type="llm.completion", params={}, runOn="gpu")

    async def _t():
        db = await engine._connect()
        try:
            return await engine._dispatch_remote(db, "default", node, None, {}, {})
        finally:
            await db.close()

    assert asyncio.run(_t()) is None


def test_dispatch_remote_routes_to_online_runner_and_returns_its_result(monkeypatch):
    asyncio.run(_seed_runner(labels=["gpu"]))
    _auto_complete_factory(monkeypatch)
    node = GraphNode(id="n1", type="set", params={"fields": {"a": 1}}, runOn="gpu")

    async def _t():
        db = await engine._connect()
        try:
            return await engine._dispatch_remote(db, "default", node, {"x": 1}, {"fields": {"a": 1}}, {"_run_id": None})
        finally:
            await db.close()

    output, handles = asyncio.run(_t())
    assert output == {"a": 1}
    assert handles == ["main"]


def test_dispatch_remote_no_matching_runner_raises_by_default():
    node = GraphNode(id="n1", type="set", params={"fields": {}}, runOn="dmz")  # no runner with this label

    async def _t():
        db = await engine._connect()
        try:
            await engine._dispatch_remote(db, "default", node, None, {"fields": {}}, {"_run_id": None})
        finally:
            await db.close()

    with pytest.raises(RuntimeError, match="no online runner"):
        asyncio.run(_t())


def test_dispatch_remote_no_matching_runner_falls_back_local_when_configured():
    node = GraphNode(id="n1", type="set", params={"fields": {"a": 1}}, runOn="dmz", runOnFallback="local")

    async def _t():
        db = await engine._connect()
        try:
            return await engine._dispatch_remote(db, "default", node, None, {"fields": {"a": 1}}, {"_run_id": None})
        finally:
            await db.close()

    output, handles = asyncio.run(_t())
    assert output == {"a": 1}
    assert handles == ["main"]


def test_dispatch_remote_allow_list_excludes_node_type():
    asyncio.run(_seed_runner(labels=["dmz"], allowed=["http.request"]))
    node = GraphNode(id="n1", type="code", params={"code": "pass"}, runOn="dmz")  # not in allow-list

    async def _t():
        db = await engine._connect()
        try:
            await engine._dispatch_remote(db, "default", node, None, {"code": "pass"}, {"_run_id": None})
        finally:
            await db.close()

    with pytest.raises(RuntimeError, match="no online runner"):
        asyncio.run(_t())


def test_dispatch_remote_job_timeout_falls_back_local(monkeypatch):
    monkeypatch.setattr(engine.settings, "graph_workflow_runner_poll_interval", 0.01)
    asyncio.run(_seed_runner(labels=["gpu"]))  # a runner is online, but never answers the job
    node = GraphNode(
        id="n1", type="set", params={"fields": {"a": 1}}, runOn="gpu",
        runOnFallback="local", timeoutMs=50,
    )

    async def _t():
        db = await engine._connect()
        try:
            return await engine._dispatch_remote(db, "default", node, None, {"fields": {"a": 1}}, {"_run_id": None})
        finally:
            await db.close()

    output, handles = asyncio.run(_t())
    assert output == {"a": 1}


def test_dispatch_remote_job_timeout_fails_without_fallback(monkeypatch):
    monkeypatch.setattr(engine.settings, "graph_workflow_runner_poll_interval", 0.01)
    asyncio.run(_seed_runner(labels=["gpu"]))
    node = GraphNode(id="n1", type="set", params={"fields": {}}, runOn="gpu", timeoutMs=50)

    async def _t():
        db = await engine._connect()
        try:
            await engine._dispatch_remote(db, "default", node, None, {"fields": {}}, {"_run_id": None})
        finally:
            await db.close()

    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(_t())


def test_workflow_run_routes_node_through_online_runner_end_to_end(
    client, auth_headers, monkeypatch, captured_spawns,
):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "remote", "type": "set", "params": {"fields": {"done": True}}, "runOn": "gpu"},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "remote"}],
    }
    wf = _make(client, auth_headers, graph)
    asyncio.run(_seed_runner(profile_id=wf["profile_id"], labels=["gpu"]))
    _auto_complete_factory(monkeypatch)

    resp = client.post(f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]
    _drive_last_run(captured_spawns)

    resp = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "completed"
    node_runs = {nr["node_id"]: nr for nr in data["node_runs"]}
    assert node_runs["remote"]["output"] == {"done": True}


# ── fase 14.2: `code` node sandbox ──────────────────────────────────────────

def test_code_node_runs_through_the_sandboxed_subprocess(monkeypatch):
    """The code node already dispatches through the Phase 18 sandbox
    (`app.tools.code_interpreter.python_exec` — a subprocess with CPU/memory/
    time limits and no network); this just pins that it stays true so a
    remote runner (14.1) inherits the same isolation for free."""
    calls = []

    async def _fake_python_exec(code, files=None):
        calls.append(code)
        return "stdout:\nok"

    monkeypatch.setattr("app.tools.code_interpreter.python_exec", _fake_python_exec)
    out = asyncio.run(engine._exec_code({"code": "print('hi')"}, None, {"node": {}}))
    assert out == {"stdout": "stdout:\nok"}
    assert calls and "print('hi')" in calls[0]


# ── fase 14.3: run lease (engine scale-out mechanism) ───────────────────────

def test_acquire_lease_then_renewal_by_same_owner_succeeds(client, auth_headers):
    wf = _make(client, auth_headers, _SIMPLE_GRAPH)

    async def _t():
        db = await engine._connect()
        try:
            run_id = await repo.create_run(db, wf["id"], wf["profile_id"], "manual", json.dumps(_SIMPLE_GRAPH))
            first = await repo.acquire_lease(db, run_id, "instance-a", 60)
            renewed = await repo.acquire_lease(db, run_id, "instance-a", 60)
            return first, renewed
        finally:
            await db.close()

    first, renewed = asyncio.run(_t())
    assert first is True
    assert renewed is True


def test_acquire_lease_blocked_by_another_live_owner_then_freed_on_release(client, auth_headers):
    wf = _make(client, auth_headers, _SIMPLE_GRAPH)

    async def _t():
        db = await engine._connect()
        try:
            run_id = await repo.create_run(db, wf["id"], wf["profile_id"], "manual", json.dumps(_SIMPLE_GRAPH))
            assert await repo.acquire_lease(db, run_id, "instance-a", 60) is True
            blocked = await repo.acquire_lease(db, run_id, "instance-b", 60)
            await repo.release_lease(db, run_id, "instance-a")
            freed = await repo.acquire_lease(db, run_id, "instance-b", 60)
            return blocked, freed
        finally:
            await db.close()

    blocked, freed = asyncio.run(_t())
    assert blocked is False
    assert freed is True


def test_acquire_lease_reclaims_an_expired_lease(client, auth_headers):
    wf = _make(client, auth_headers, _SIMPLE_GRAPH)

    async def _t():
        db = await engine._connect()
        try:
            run_id = await repo.create_run(db, wf["id"], wf["profile_id"], "manual", json.dumps(_SIMPLE_GRAPH))
            # Simulate a lease left behind by a crashed instance: expires_at in the past.
            await db.execute(
                "UPDATE workflow_runs SET lease_owner = ?, lease_expires_at = ? WHERE id = ?",
                ("dead-instance", 1, run_id),
            )
            await db.commit()
            return await repo.acquire_lease(db, run_id, "instance-b", 60)
        finally:
            await db.close()

    assert asyncio.run(_t()) is True


def test_run_execution_skips_when_another_live_instance_holds_the_lease(
    client, auth_headers, captured_spawns,
):
    wf = _make(client, auth_headers, _SIMPLE_GRAPH)
    resp = client.post(f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers)
    run_id = resp.json()["run_id"]

    async def _steal_lease():
        db = await engine._connect()
        try:
            await repo.acquire_lease(db, run_id, "some-other-live-instance", 300)
        finally:
            await db.close()

    asyncio.run(_steal_lease())
    _drive_last_run(captured_spawns)  # _execute should bail out immediately

    resp = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers)
    data = resp.json()
    # Never got past "pending" — this instance declined to run it.
    assert data["status"] == "pending"


# ── fase 14.4: message queue triggers ───────────────────────────────────────

def test_db_queue_driver_publish_then_consume_round_trip():
    async def _t():
        driver = engine.get_queue_driver()
        await driver.publish("orders", {"id": 1}, {"trace": "abc"})
        msgs = await driver.consume("orders", 10)
        return msgs

    msgs = asyncio.run(_t())
    assert len(msgs) == 1
    assert msgs[0]["message"] == {"id": 1}
    assert msgs[0]["headers"] == {"trace": "abc"}
    # Consumed messages are not redelivered.
    again = asyncio.run(engine.get_queue_driver().consume("orders", 10))
    assert again == []


def test_memory_queue_driver_round_trip(monkeypatch):
    monkeypatch.setattr(engine.settings, "graph_workflow_queue_driver", "memory")

    async def _t():
        driver = engine.get_queue_driver()
        assert isinstance(driver, engine._MemoryQueueDriver)
        await driver.publish("orders-mem", {"id": 2}, {})
        return await driver.consume("orders-mem", 10)

    msgs = asyncio.run(_t())
    assert msgs[0]["message"] == {"id": 2}


def test_queue_publish_node_dispatch():
    async def _t():
        out, handles = await engine._dispatch_stateless(
            "queue.publish", {"topic": "notify", "message": {"hi": True}}, None,
        )
        return out, handles

    out, handles = asyncio.run(_t())
    assert out == {"topic": "notify", "published": True}
    assert handles == ["main"]
    consumed = asyncio.run(engine.get_queue_driver().consume("notify", 10))
    assert consumed[0]["message"] == {"hi": True}


def test_queue_publish_requires_topic():
    with pytest.raises(ValueError, match="topic"):
        asyncio.run(engine._exec_queue_publish({}, None))


def test_queue_consume_trigger_fires_a_run(client, auth_headers, captured_spawns):
    wf = _make(client, auth_headers, _SIMPLE_GRAPH)
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "queue.consume", "config": {"topic": "orders-trg"}, "enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    client.post(f"/api/v1/graph-workflows/{wf['id']}/activate", headers=auth_headers)

    asyncio.run(engine.get_queue_driver().publish("orders-trg", {"order_id": 7}, {}))

    async def _poll():
        db = await engine._connect()
        try:
            due = await repo.list_due_poll_triggers(db, 10 ** 12)
            row = next(r for r in due if r["workflow_id"] == wf["id"])
            cfg = json.loads(row["config_json"])
            return await engine._poll_queue_consume(db, row, cfg)
        finally:
            await db.close()

    fired = asyncio.run(_poll())
    assert fired is True
    assert len(captured_spawns) == 1
    args, _kwargs = captured_spawns[0]
    assert args[4] == {"message": {"order_id": 7}, "topic": "orders-trg", "headers": {}}
    assert args[3] == "queue.consume"


# ── fase 14.5: `sibyl-wf` CLI ────────────────────────────────────────────────

class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _cli_client(auth_headers):
    from app.main import app as fastapi_app

    token = auth_headers["Authorization"].split(" ", 1)[1]
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url="http://testserver/api/v1",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )


def test_cli_run_export_import_test_logs(client, auth_headers, tmp_path):
    wf = _make(client, auth_headers, _SIMPLE_GRAPH, name="cli wf")

    async def _t():
        async with _cli_client(auth_headers) as cli_client:
            # run
            rc = await sibyl_wf.cmd_run(cli_client, _Args(workflow_id=wf["id"], trigger=None))
            assert rc == 0

            resp = client.get(f"/api/v1/graph-workflows/{wf['id']}/runs", headers=auth_headers)
            run_id = resp.json()[0]["id"]

            # logs
            rc = await sibyl_wf.cmd_logs(cli_client, _Args(run_id=run_id))
            assert rc == 0

            # test (single node, fase 3.1)
            rc = await sibyl_wf.cmd_test(cli_client, _Args(workflow_id=wf["id"], node_id="out", input=None))
            assert rc == 0

            # export
            out_file = tmp_path / "exported.json"
            rc = await sibyl_wf.cmd_export(cli_client, _Args(workflow_id=wf["id"], out=str(out_file)))
            assert rc == 0
            assert out_file.exists()
            exported = json.loads(out_file.read_text())
            assert exported["name"] == "cli wf"

            # import
            exported["name"] = "cli wf (imported)"
            in_file = tmp_path / "to_import.json"
            in_file.write_text(json.dumps(exported))
            rc = await sibyl_wf.cmd_import(cli_client, _Args(file=str(in_file)))
            assert rc == 0

    asyncio.run(_t())

    resp = client.get("/api/v1/graph-workflows", headers=auth_headers)
    names = {w["name"] for w in resp.json()}
    assert "cli wf (imported)" in names


def test_cli_requires_api_key(capsys):
    with pytest.raises(SystemExit):
        sibyl_wf.main(["run", "some-id"])
