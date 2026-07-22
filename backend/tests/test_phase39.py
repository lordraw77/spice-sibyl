"""
Phase 39 — roadmap fase 7: operations and governance.

Covers retry-from-failed-node and run lineage (7.1), named environments with
vars/secrets bindings and version promotion (7.2), the per-workflow audit trail
and workspace share roles (7.3), per-node health metrics (7.4) and the inline
Telegram approve/reject buttons (7.5).
"""

import asyncio
import uuid
from types import SimpleNamespace

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


_SIMPLE_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "out", "type": "set", "params": {"fields": {"done": True}}},
    ],
    "edges": [{"id": "e1", "source": "t", "target": "out"}],
}

# ok node succeeds, boom node fails (invalid URL raises before any I/O) → the
# run fails with a checkpointed output for t/ok and no entry for boom.
_FAILING_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "ok", "type": "set", "params": {"fields": {"step": "ok"}}},
        {"id": "boom", "type": "http.request", "params": {"url": "not-a-url"}},
    ],
    "edges": [
        {"id": "e1", "source": "t", "target": "ok"},
        {"id": "e2", "source": "ok", "target": "boom"},
    ],
}


# ── fase 7.1: retry from the failed node ─────────────────────────────────────

def test_retry_failed_run_reexecutes_only_missing_subgraph(client, auth_headers, captured_spawns):
    wf = _make_wf(client, auth_headers, "retry flow", _FAILING_GRAPH)
    run_id = _run(client, auth_headers, wf["id"])
    _drive_last_run(captured_spawns)
    origin = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    assert origin["status"] == "failed"

    resp = client.post(f"/api/v1/graph-workflows/runs/{run_id}/retry", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    new_run_id = resp.json()["run_id"]
    assert new_run_id != run_id
    _drive_last_run(captured_spawns)

    retried = client.get(f"/api/v1/graph-workflows/runs/{new_run_id}", headers=auth_headers).json()
    assert retried["origin_run_id"] == run_id
    assert retried["status"] == "failed"  # same graph, same failing node
    # Only the failed node re-executed: t/ok were seeded from the checkpoint.
    executed = {nr["node_id"] for nr in retried["node_runs"]}
    assert executed == {"boom"}


def test_retry_rejected_for_non_failed_runs(client, auth_headers, captured_spawns):
    wf = _make_wf(client, auth_headers, "retry-ok flow", _SIMPLE_GRAPH)
    run_id = _run(client, auth_headers, wf["id"])
    _drive_last_run(captured_spawns)
    resp = client.post(f"/api/v1/graph-workflows/runs/{run_id}/retry", headers=auth_headers)
    assert resp.status_code == 409


def test_replay_records_origin_run(client, auth_headers, captured_spawns):
    wf = _make_wf(client, auth_headers, "replay lineage flow", _SIMPLE_GRAPH)
    run_id = _run(client, auth_headers, wf["id"])
    _drive_last_run(captured_spawns)
    resp = client.post(f"/api/v1/graph-workflows/runs/{run_id}/replay", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    replayed = client.get(
        f"/api/v1/graph-workflows/runs/{resp.json()['run_id']}", headers=auth_headers
    ).json()
    assert replayed["origin_run_id"] == run_id


# ── fase 7.2: environments ───────────────────────────────────────────────────

_ENV_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "out", "type": "set", "params": {"fields": {"msg": "={{ $vars.greeting }}"}}},
    ],
    "edges": [{"id": "e1", "source": "t", "target": "out"}],
}


def _node_output(client, auth_headers, run_id, node_id):
    run = client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()
    return next(nr for nr in run["node_runs"] if nr["node_id"] == node_id)["output"]


def test_environment_vars_overlay_workflow_vars(client, auth_headers, captured_spawns):
    wf = _make_wf(
        client, auth_headers, "env vars flow", _ENV_GRAPH,
        variables={"greeting": "dev"},
        environments={"prod": {"vars": {"greeting": "prodval"}}},
    )
    default_run = _run(client, auth_headers, wf["id"])
    _drive_last_run(captured_spawns)
    assert _node_output(client, auth_headers, default_run, "out") == {"msg": "dev"}

    prod_run = _run(client, auth_headers, wf["id"], {"payload": {}, "environment": "prod"})
    _drive_last_run(captured_spawns)
    assert _node_output(client, auth_headers, prod_run, "out") == {"msg": "prodval"}
    run = client.get(f"/api/v1/graph-workflows/runs/{prod_run}", headers=auth_headers).json()
    assert run["environment"] == "prod"


def test_undefined_environment_rejected(client, auth_headers):
    wf = _make_wf(client, auth_headers, "env missing flow", _SIMPLE_GRAPH)
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run",
        json={"payload": {}, "environment": "prod"}, headers=auth_headers,
    )
    assert resp.status_code == 400


def test_environment_secret_bindings_remap_aliases(client, auth_headers, captured_spawns):
    client.put("/api/v1/graph-workflows/secrets",
               json={"name": "P39_TOKEN", "value": "dev-secret"}, headers=auth_headers)
    client.put("/api/v1/graph-workflows/secrets",
               json={"name": "P39_TOKEN_PROD", "value": "prod-secret"}, headers=auth_headers)
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "out", "type": "set", "params": {"fields": {"tok": "={{ $secrets.P39_TOKEN }}"}}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "out"}],
    }
    wf = _make_wf(
        client, auth_headers, "env secrets flow", graph,
        environments={"prod": {"secrets": {"P39_TOKEN": "P39_TOKEN_PROD"}}},
    )
    dev_run = _run(client, auth_headers, wf["id"])
    _drive_last_run(captured_spawns)
    assert _node_output(client, auth_headers, dev_run, "out") == {"tok": "dev-secret"}

    prod_run = _run(client, auth_headers, wf["id"], {"payload": {}, "environment": "prod"})
    _drive_last_run(captured_spawns)
    assert _node_output(client, auth_headers, prod_run, "out") == {"tok": "prod-secret"}


def test_promote_pins_a_version_for_the_environment(client, auth_headers, captured_spawns):
    wf = _make_wf(client, auth_headers, "promote flow", _SIMPLE_GRAPH)  # v1: done=True
    v2_graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "out", "type": "set", "params": {"fields": {"done": "v2"}}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "out"}],
    }
    resp = client.patch(f"/api/v1/graph-workflows/{wf['id']}", json={"graph": v2_graph},
                        headers=auth_headers)
    assert resp.json()["version"] == 2

    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/environments/prod/promote",
        json={"version": 1}, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["environments"]["prod"]["version"] == 1

    prod_run = _run(client, auth_headers, wf["id"], {"payload": {}, "environment": "prod"})
    _drive_last_run(captured_spawns)
    assert _node_output(client, auth_headers, prod_run, "out") == {"done": True}  # v1

    current_run = _run(client, auth_headers, wf["id"])
    _drive_last_run(captured_spawns)
    assert _node_output(client, auth_headers, current_run, "out") == {"done": "v2"}


def test_promote_unknown_version_404(client, auth_headers):
    wf = _make_wf(client, auth_headers, "promote 404 flow", _SIMPLE_GRAPH)
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/environments/prod/promote",
        json={"version": 99}, headers=auth_headers,
    )
    assert resp.status_code == 404


# ── fase 5.1 (extended): stats scoped to one environment ────────────────────

def test_stats_can_be_scoped_to_an_environment(client, auth_headers, captured_spawns):
    wf = _make_wf(
        client, auth_headers, "stats env flow", _SIMPLE_GRAPH,
        environments={"prod": {}},
    )
    _run(client, auth_headers, wf["id"])  # default environment
    _drive_last_run(captured_spawns)
    _run(client, auth_headers, wf["id"], {"payload": {}, "environment": "prod"})
    _drive_last_run(captured_spawns)
    _run(client, auth_headers, wf["id"], {"payload": {}, "environment": "prod"})
    _drive_last_run(captured_spawns)

    unfiltered = client.get("/api/v1/graph-workflows/stats", headers=auth_headers).json()
    row = next(s for s in unfiltered if s["workflow_id"] == wf["id"])
    assert row["runs"] == 3

    prod_only = client.get(
        "/api/v1/graph-workflows/stats?environment=prod", headers=auth_headers
    ).json()
    row = next(s for s in prod_only if s["workflow_id"] == wf["id"])
    assert row["runs"] == 2
    assert row["completed"] == 2

    # A workflow with zero runs in a given environment still appears (0 runs),
    # not omitted — same shape as the unfiltered endpoint.
    other = _make_wf(client, auth_headers, "stats env flow (no prod runs)", _SIMPLE_GRAPH)
    prod_only = client.get(
        "/api/v1/graph-workflows/stats?environment=prod", headers=auth_headers
    ).json()
    row = next(s for s in prod_only if s["workflow_id"] == other["id"])
    assert row["runs"] == 0


# ── fase 7.3: per-workflow audit trail ───────────────────────────────────────

def test_workflow_audit_trail(client, auth_headers, captured_spawns):
    wf = _make_wf(client, auth_headers, "audit flow", _SIMPLE_GRAPH)
    client.post(f"/api/v1/graph-workflows/{wf['id']}/activate", headers=auth_headers)
    _run(client, auth_headers, wf["id"])
    _drive_last_run(captured_spawns)

    resp = client.get(f"/api/v1/graph-workflows/{wf['id']}/audit", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    actions = [e["action"] for e in resp.json()]
    assert "graph_workflow.create" in actions
    assert "graph_workflow.activate" in actions
    assert "graph_workflow.run" in actions


# ── fase 7.3: workspace share roles ──────────────────────────────────────────

def _register_and_login(client, auth_headers, email, password="member-pass-123"):
    client.post(
        "/api/v1/auth/register", headers=auth_headers,
        json={"email": email, "password": password, "role": "user"},
    )
    tok = client.post("/api/v1/auth/login", json={"email": email, "password": password}).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


def test_share_role_gates_runs_and_approvals(client, auth_headers, captured_spawns):
    wf = _make_wf(client, auth_headers, "shared roles flow", _SIMPLE_GRAPH)
    ws = client.post("/api/v1/workspaces", headers=auth_headers, json={"name": "P39"}).json()
    member_email = f"p39-{uuid.uuid4().hex[:6]}@example.com"
    member_headers = _register_and_login(client, auth_headers, member_email)
    client.post(f"/api/v1/workspaces/{ws['id']}/members", headers=auth_headers,
                json={"email": member_email, "role": "viewer"})

    # Shared as viewer: the member cannot launch runs.
    client.post(f"/api/v1/workspaces/{ws['id']}/workflows", headers=auth_headers,
                json={"workflow_id": wf["id"], "role": "viewer"})
    shared = client.get(f"/api/v1/workspaces/{ws['id']}/workflows", headers=member_headers).json()
    assert shared[0]["role"] == "viewer"
    resp = client.post(f"/api/v1/workspaces/{ws['id']}/workflows/{wf['id']}/run",
                       headers=member_headers, json={"payload": {}})
    assert resp.status_code == 403

    # Re-sharing as editor upgrades the role in place; runs are now allowed and
    # execute under the OWNER's profile.
    client.post(f"/api/v1/workspaces/{ws['id']}/workflows", headers=auth_headers,
                json={"workflow_id": wf["id"], "role": "editor"})
    resp = client.post(f"/api/v1/workspaces/{ws['id']}/workflows/{wf['id']}/run",
                       headers=member_headers, json={"payload": {}})
    assert resp.status_code == 200, resp.text
    _drive_last_run(captured_spawns)
    runs = client.get(f"/api/v1/graph-workflows/{wf['id']}/runs", headers=auth_headers).json()
    assert runs and runs[0]["status"] == "completed"

    # An approver (and only an approver) may decide the workflow's approvals.
    run_id = _run(client, auth_headers, wf["id"])

    async def _make_approval():
        db = await engine._connect()
        try:
            from app.db import graph_workflow_repository as gw_repo

            return (await gw_repo.create_approval(
                db, run_id, "gate", wf["id"], wf["profile_id"],
                title="Gate", message="Decide", timeout_at=None,
            )).id
        finally:
            await db.close()

    approval_id = asyncio.run(_make_approval())
    resp = client.post(f"/api/v1/graph-workflows/approvals/{approval_id}/decision",
                       headers=member_headers, json={"approved": True})
    assert resp.status_code == 404  # editor is not enough

    client.post(f"/api/v1/workspaces/{ws['id']}/workflows", headers=auth_headers,
                json={"workflow_id": wf["id"], "role": "approver"})
    resp = client.post(f"/api/v1/graph-workflows/approvals/{approval_id}/decision",
                       headers=member_headers, json={"approved": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"


# ── fase 7.4: per-node health metrics ────────────────────────────────────────

def test_node_stats_aggregate_outcomes_and_durations(client, auth_headers, captured_spawns):
    wf = _make_wf(client, auth_headers, "health flow", _FAILING_GRAPH)
    for _ in range(2):
        _run(client, auth_headers, wf["id"])
        _drive_last_run(captured_spawns)

    resp = client.get(f"/api/v1/graph-workflows/{wf['id']}/stats/nodes", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    stats = {s["node_id"]: s for s in resp.json()}
    assert stats["ok"]["executions"] == 2
    assert stats["ok"]["error"] == 0
    assert stats["ok"]["error_rate"] == 0
    assert stats["boom"]["error"] == 2
    assert stats["boom"]["error_rate"] == 1.0
    assert stats["boom"]["p50_duration_s"] is not None
    assert stats["boom"]["p95_duration_s"] is not None
    # The unhealthiest node sorts first.
    assert resp.json()[0]["node_id"] == "boom"


# ── fase 7.5: Telegram approval buttons ──────────────────────────────────────

def test_approval_notification_carries_inline_buttons(monkeypatch):
    from app.services import notification_service

    sent: dict = {}

    async def _fake_web(db, profile_id, event_type, title, body="", meta=None):
        sent["web"] = title

    async def _fake_tg(db, profile_id, event_type, text, parse_mode=None, buttons=None):
        sent["buttons"] = buttons

    monkeypatch.setattr(notification_service, "notify_web", _fake_web)
    monkeypatch.setattr(notification_service, "notify_telegram", _fake_tg)

    approval = SimpleNamespace(id="ap-1", title="Deploy?", message="Please decide")
    asyncio.run(engine._notify_approval_request(None, "default", approval, {"telegram": True}))
    assert sent["buttons"] == [[("✅ Approve", "wfap:a:ap-1"), ("❌ Reject", "wfap:r:ap-1")]]


def test_telegram_callback_decides_pending_approval(client, auth_headers, captured_spawns):
    """The bot handler's core contract — a decision via repo is equivalent to the
    API's and the first writer wins — exercised at the repository level (driving
    python-telegram-bot updates in tests is out of scope)."""
    wf = _make_wf(client, auth_headers, "tg decision flow", _SIMPLE_GRAPH)
    run_id = _run(client, auth_headers, wf["id"])
    _drive_last_run(captured_spawns)

    async def _flow():
        db = await engine._connect()
        try:
            from app.db import graph_workflow_repository as gw_repo

            approval = await gw_repo.create_approval(
                db, run_id, "gate", wf["id"], wf["profile_id"],
                title="Gate", message="Decide", timeout_at=None,
            )
            first = await gw_repo.decide_approval(
                db, approval.id, status="approved", decided_by="telegram:42", comment="via Telegram"
            )
            second = await gw_repo.decide_approval(db, approval.id, status="rejected")
            return first, second, await gw_repo.get_approval(db, approval.id)
        finally:
            await db.close()

    first, second, final = asyncio.run(_flow())
    assert first is True
    assert second is False  # first writer wins
    assert final.status == "approved"
    assert final.decided_by == "telegram:42"
