"""
Phase 38 — roadmap fase 6: engine extension (triggers, loops, composition).

Covers the `success` trigger + multi-cron schedules (6.1), the poll-based
`file.watch` / `email.inbound` triggers (6.2), the `while` loop node (6.3),
sub-workflow contracts + callable `workflow.<id>` catalog nodes (6.4), the
`kb.search` node (6.5) and per-host rate limiting of http.request (6.6).
"""

import asyncio
import json
import time

import pytest

from app.core.config import settings
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


_SIMPLE_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "out", "type": "set", "params": {"fields": {"done": True}}},
    ],
    "edges": [{"id": "e1", "source": "t", "target": "out"}],
}


# ── fase 6.1: success trigger ────────────────────────────────────────────────

def test_success_trigger_fires_watcher_workflow(client, auth_headers, captured_spawns):
    source = _make_wf(client, auth_headers, "source flow", _SIMPLE_GRAPH)
    watcher = _make_wf(client, auth_headers, "watcher flow", _SIMPLE_GRAPH)
    resp = client.post(
        f"/api/v1/graph-workflows/{watcher['id']}/triggers",
        json={"type": "success", "config": {"workflow_id": source["id"]}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    client.post(f"/api/v1/graph-workflows/{watcher['id']}/activate", headers=auth_headers)

    client.post(f"/api/v1/graph-workflows/{source['id']}/run", json={"payload": {}},
                headers=auth_headers)
    _drive_last_run(captured_spawns)  # source completes → success trigger fires
    assert len(captured_spawns) == 2  # the watcher run was spawned
    _drive_last_run(captured_spawns)

    runs = client.get(f"/api/v1/graph-workflows/{watcher['id']}/runs", headers=auth_headers).json()
    assert len(runs) == 1
    assert runs[0]["trigger_type"] == "success"
    assert runs[0]["status"] == "completed"
    run = client.get(f"/api/v1/graph-workflows/runs/{runs[0]['id']}", headers=auth_headers).json()
    trigger_input = next(nr for nr in run["node_runs"] if nr["node_id"] == "t")["input"]
    assert trigger_input["workflow_id"] == source["id"]
    assert trigger_input["run_id"]
    assert trigger_input["output"] == {"done": True}


def test_success_trigger_never_cascades_or_self_fires(client, auth_headers, captured_spawns):
    source = _make_wf(client, auth_headers, "cascade source", _SIMPLE_GRAPH)
    # Watching itself must never fire (guard in list_success_triggers).
    client.post(
        f"/api/v1/graph-workflows/{source['id']}/triggers",
        json={"type": "success", "config": {}}, headers=auth_headers,
    )
    client.post(f"/api/v1/graph-workflows/{source['id']}/activate", headers=auth_headers)
    client.post(f"/api/v1/graph-workflows/{source['id']}/run", json={"payload": {}},
                headers=auth_headers)
    _drive_last_run(captured_spawns)
    assert len(captured_spawns) == 1  # no self-fire


# ── fase 6.1: multiple cron expressions ──────────────────────────────────────

def test_schedule_trigger_accepts_multiple_crons(client, auth_headers):
    wf = _make_wf(client, auth_headers, "multicron flow", _SIMPLE_GRAPH)
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "schedule",
              "config": {"pattern": "cron", "crons": ["0 9 * * 1-5", "0 12 * * 6"]}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    trigger = resp.json()
    assert trigger["config"]["recurrence"].startswith("crons:")
    assert "|" in trigger["config"]["recurrence"]
    assert trigger["next_run_at"] is not None


def test_schedule_trigger_rejects_bad_cron_in_list(client, auth_headers):
    wf = _make_wf(client, auth_headers, "badcron flow", _SIMPLE_GRAPH)
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "schedule", "config": {"pattern": "cron", "crons": ["0 9 * * 1-5", "nope"]}},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_compute_next_fire_multicron_picks_earliest():
    from zoneinfo import ZoneInfo

    from app.services import reminder_parsing

    tz = ZoneInfo("UTC")
    now = int(time.time())
    # every hour at :00 vs every day at 23:59 — the hourly one comes first
    multi = reminder_parsing.compute_next_fire("crons:0,*,*,*,*|59,23,*,*,*", now, tz)
    hourly = reminder_parsing.compute_next_fire("cron:0,*,*,*,*", now, tz)
    assert multi == hourly


# ── fase 6.2: file.watch trigger ─────────────────────────────────────────────

def test_file_watch_trigger_fires_on_new_file(client, auth_headers, captured_spawns,
                                              monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "graph_workflow_files_dir", str(tmp_path))
    wf = _make_wf(client, auth_headers, "watch flow", _SIMPLE_GRAPH)
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "file.watch", "config": {"path": "inbox", "pattern": "*.txt"}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    client.post(f"/api/v1/graph-workflows/{wf['id']}/activate", headers=auth_headers)
    (tmp_path / "inbox").mkdir()

    async def _poll():
        db = await engine._connect()
        try:
            await engine._poll_watch_triggers(db)
        finally:
            await db.close()

    asyncio.run(_poll())          # first poll: seeds the snapshot, no fire
    assert len(captured_spawns) == 0

    (tmp_path / "inbox" / "report.txt").write_text("hello")
    (tmp_path / "inbox" / "ignored.csv").write_text("nope")  # outside the glob

    # make the trigger due again (next_run_at was pushed into the future)
    trigger = client.get(f"/api/v1/graph-workflows/{wf['id']}/triggers", headers=auth_headers).json()[0]

    async def _force_due_and_poll():
        db = await engine._connect()
        try:
            from app.db import graph_workflow_repository as repo
            await repo.set_trigger_next_run(db, trigger["id"], int(time.time()) - 1)
            await engine._poll_watch_triggers(db)
        finally:
            await db.close()

    asyncio.run(_force_due_and_poll())
    assert len(captured_spawns) == 1
    _drive_last_run(captured_spawns)
    runs = client.get(f"/api/v1/graph-workflows/{wf['id']}/runs", headers=auth_headers).json()
    assert runs[0]["trigger_type"] == "file.watch"
    run = client.get(f"/api/v1/graph-workflows/runs/{runs[0]['id']}", headers=auth_headers).json()
    trigger_input = next(nr for nr in run["node_runs"] if nr["node_id"] == "t")["input"]
    assert trigger_input["path"] == "inbox/report.txt"
    assert trigger_input["event"] == "created"
    assert trigger_input["size"] == 5


# ── fase 6.2: email.inbound trigger ──────────────────────────────────────────

def test_email_inbound_trigger_requires_host(client, auth_headers):
    wf = _make_wf(client, auth_headers, "mail flow", _SIMPLE_GRAPH)
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "email.inbound", "config": {}}, headers=auth_headers,
    )
    assert resp.status_code == 400


def test_email_inbound_poll_fires_run_with_message(client, auth_headers, captured_spawns,
                                                   monkeypatch, tmp_path):
    from email.message import EmailMessage

    monkeypatch.setattr(settings, "graph_workflow_files_dir", str(tmp_path))
    wf = _make_wf(client, auth_headers, "imap flow", _SIMPLE_GRAPH)
    client.put("/api/v1/graph-workflows/secrets",
               json={"name": "IMAP_PASS", "value": "s3cret"}, headers=auth_headers)
    resp = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "email.inbound",
              "config": {"host": "imap.example.com", "username": "bot@example.com",
                         "password_secret": "IMAP_PASS", "subject": "invoice"}},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    client.post(f"/api/v1/graph-workflows/{wf['id']}/activate", headers=auth_headers)

    msg = EmailMessage()
    msg["From"] = "Alice <alice@example.com>"
    msg["Subject"] = "Invoice #42"
    msg.set_content("please find attached")
    msg.add_attachment(b"PDFDATA", maintype="application", subtype="pdf", filename="invoice.pdf")
    raw = msg.as_bytes()

    class FakeIMAP:
        def __init__(self, host, port, timeout=None):
            assert host == "imap.example.com"

        def login(self, user, password):
            assert user == "bot@example.com" and password == "s3cret"

        def select(self, folder):
            assert folder == "INBOX"

        def search(self, charset, criterion):
            return "OK", [b"1"]

        def fetch(self, mid, spec):
            return "OK", [(b"1 (RFC822)", raw)]

        def logout(self):
            return "BYE", []

    import imaplib

    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)

    async def _poll():
        db = await engine._connect()
        try:
            await engine._poll_watch_triggers(db)
        finally:
            await db.close()

    asyncio.run(_poll())
    assert len(captured_spawns) == 1
    _drive_last_run(captured_spawns)
    runs = client.get(f"/api/v1/graph-workflows/{wf['id']}/runs", headers=auth_headers).json()
    assert runs[0]["trigger_type"] == "email.inbound"
    run = client.get(f"/api/v1/graph-workflows/runs/{runs[0]['id']}", headers=auth_headers).json()
    trigger_input = next(nr for nr in run["node_runs"] if nr["node_id"] == "t")["input"]
    assert "alice@example.com" in trigger_input["from"]
    assert trigger_input["subject"] == "Invoice #42"
    assert "attached" in trigger_input["body"]
    assert len(trigger_input["attachments"]) == 1
    saved = tmp_path / trigger_input["attachments"][0]
    assert saved.read_bytes() == b"PDFDATA"


# ── fase 6.3: while loop ─────────────────────────────────────────────────────

def test_while_loop_runs_until_condition_false(client, auth_headers, captured_spawns):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "w", "type": "while",
             "params": {"condition": "={{ $index < 3 }}", "maxIterations": 10}},
            {"id": "body", "type": "set", "params": {"fields": {"i": "={{ $index }}"}}},
            {"id": "after", "type": "set", "params": {"fields": {"n": "={{ $node.w.output.count }}"}}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "w"},
            {"id": "e2", "source": "w", "target": "body", "sourceHandle": "loop"},
            {"id": "e3", "source": "w", "target": "after", "sourceHandle": "done"},
        ],
    }
    wf = _make_wf(client, auth_headers, "while flow", graph)
    client.post(f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers)
    _drive_last_run(captured_spawns)

    runs = client.get(f"/api/v1/graph-workflows/{wf['id']}/runs", headers=auth_headers).json()
    assert runs[0]["status"] == "completed"
    run = client.get(f"/api/v1/graph-workflows/runs/{runs[0]['id']}", headers=auth_headers).json()
    w = next(nr for nr in run["node_runs"] if nr["node_id"] == "w")
    assert w["output"]["count"] == 3
    assert w["output"]["items"] == [{"i": 0}, {"i": 1}, {"i": 2}]
    assert w["output"]["capped"] is False
    after = next(nr for nr in run["node_runs"] if nr["node_id"] == "after")
    assert after["output"] == {"n": 3}


def test_while_loop_iteration_cap(client, auth_headers, captured_spawns):
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "w", "type": "while",
             "params": {"condition": "={{ 1 == 1 }}", "maxIterations": 4}},
            {"id": "body", "type": "set", "params": {"fields": {"i": "={{ $index }}"}}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "w"},
            {"id": "e2", "source": "w", "target": "body", "sourceHandle": "loop"},
        ],
    }
    wf = _make_wf(client, auth_headers, "capped while", graph)
    client.post(f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": {}}, headers=auth_headers)
    _drive_last_run(captured_spawns)
    runs = client.get(f"/api/v1/graph-workflows/{wf['id']}/runs", headers=auth_headers).json()
    run = client.get(f"/api/v1/graph-workflows/runs/{runs[0]['id']}", headers=auth_headers).json()
    w = next(nr for nr in run["node_runs"] if nr["node_id"] == "w")
    assert w["output"]["count"] == 4
    assert w["output"]["capped"] is True


# ── fase 6.4: sub-workflow contracts ─────────────────────────────────────────

_CHILD_SCHEMA = {
    "type": "object",
    "required": ["n"],
    "properties": {"n": {"type": "number", "description": "a number"}},
}


def _parent_graph(child_id, payload):
    return {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "sub", "type": "subworkflow",
             "params": {"workflow_id": child_id, "payload": payload}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "sub"}],
    }


def test_subworkflow_input_contract_rejects_bad_payload(client, auth_headers, captured_spawns):
    child = _make_wf(client, auth_headers, "typed child", _SIMPLE_GRAPH,
                     input_schema=_CHILD_SCHEMA)
    parent = _make_wf(client, auth_headers, "parent bad",
                      _parent_graph(child["id"], {"wrong": 1}))
    client.post(f"/api/v1/graph-workflows/{parent['id']}/run", json={"payload": {}}, headers=auth_headers)
    _drive_last_run(captured_spawns)
    runs = client.get(f"/api/v1/graph-workflows/{parent['id']}/runs", headers=auth_headers).json()
    assert runs[0]["status"] == "failed"
    assert "input contract" in runs[0]["error"]
    assert "missing required property 'n'" in runs[0]["error"]


def test_subworkflow_contract_accepts_valid_payload_and_validates_output(client, auth_headers, captured_spawns):
    child = _make_wf(client, auth_headers, "typed child ok", _SIMPLE_GRAPH,
                     input_schema=_CHILD_SCHEMA,
                     output_schema={"type": "object", "required": ["done"]})
    parent = _make_wf(client, auth_headers, "parent ok",
                      _parent_graph(child["id"], {"n": 7}))
    client.post(f"/api/v1/graph-workflows/{parent['id']}/run", json={"payload": {}}, headers=auth_headers)
    _drive_last_run(captured_spawns)
    runs = client.get(f"/api/v1/graph-workflows/{parent['id']}/runs", headers=auth_headers).json()
    assert runs[0]["status"] == "completed", runs[0]["error"]


def test_callable_workflow_appears_in_catalog_and_executes(client, auth_headers, captured_spawns):
    child = _make_wf(client, auth_headers, "Callable child", _SIMPLE_GRAPH,
                     input_schema=_CHILD_SCHEMA)
    types = client.get("/api/v1/graph-workflows/node-types", headers=auth_headers).json()
    entry = next(t for t in types if t["type"] == f"workflow.{child['id']}")
    assert entry["label"] == "Callable child"
    assert entry["category"] == "action"
    assert [p["name"] for p in entry["params_schema"]] == ["n"]

    parent = _make_wf(client, auth_headers, "typed caller", {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "call", "type": f"workflow.{child['id']}", "params": {"n": 3}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "call"}],
    })
    client.post(f"/api/v1/graph-workflows/{parent['id']}/run", json={"payload": {}}, headers=auth_headers)
    _drive_last_run(captured_spawns)
    runs = client.get(f"/api/v1/graph-workflows/{parent['id']}/runs", headers=auth_headers).json()
    assert runs[0]["status"] == "completed", runs[0]["error"]


def test_contracts_travel_with_export(client, auth_headers):
    wf = _make_wf(client, auth_headers, "contract export", _SIMPLE_GRAPH,
                  input_schema=_CHILD_SCHEMA)
    export = client.get(f"/api/v1/graph-workflows/{wf['id']}/export", headers=auth_headers).json()
    assert export["input_schema"] == _CHILD_SCHEMA
    imported = client.post("/api/v1/graph-workflows/import", json=export, headers=auth_headers).json()
    assert imported["workflow"]["input_schema"] == _CHILD_SCHEMA


def test_validate_json_schema_subset():
    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "kind": {"enum": ["a", "b"]},
        },
    }
    assert engine._validate_json_schema({"name": "x", "tags": ["y"], "kind": "a"}, schema) == []
    errors = engine._validate_json_schema({"tags": [1], "kind": "c"}, schema)
    joined = "; ".join(errors)
    assert "missing required property 'name'" in joined
    assert "$.tags[0]" in joined
    assert "not one of" in joined
    assert engine._validate_json_schema("nope", schema)[0].startswith("$: expected type object")


# ── fase 6.5: kb.search node ─────────────────────────────────────────────────

def test_kb_search_node_returns_structured_hits(monkeypatch):
    from app.schemas.knowledge import RagSource
    from app.services import rag_service

    captured = {}

    async def _fake_retrieve(db, profile_id, query, top_k=4, min_score=0.2, document_ids=None):
        captured.update(query=query, top_k=top_k, document_ids=document_ids)
        return [RagSource(document_id="d1", filename="doc.md", chunk_index=0,
                          score=0.91234, snippet="hello world")]

    monkeypatch.setattr(rag_service, "retrieve", _fake_retrieve)
    out = asyncio.run(engine._exec_kb_search(
        None, "default", {"query": "greeting", "top_k": 3, "document_ids": "d1, d2"}, None
    ))
    assert captured == {"query": "greeting", "top_k": 3, "document_ids": ["d1", "d2"]}
    assert out["count"] == 1
    assert out["results"][0] == {
        "text": "hello world", "score": 0.9123, "source": "doc.md", "chunk_index": 0,
    }


def test_kb_search_requires_a_query():
    with pytest.raises(ValueError):
        asyncio.run(engine._exec_kb_search(None, "default", {}, None))


# ── fase 6.6: per-host rate limiting ─────────────────────────────────────────

def test_parse_rate_limits_pairs_and_json():
    assert engine._parse_rate_limits("api.github.com=30, slack.com=50") == {
        "api.github.com": 30, "slack.com": 50,
    }
    assert engine._parse_rate_limits('{"api.example.com": 10}') == {"api.example.com": 10}
    assert engine._parse_rate_limits("") == {}
    assert engine._parse_rate_limits("broken") == {}


def test_host_rate_limit_picks_the_stricter_cap(monkeypatch):
    monkeypatch.setattr(engine, "_global_rate_limits", {"api.example.com": 10})
    assert engine._host_rate_limit("api.example.com", 30) == 10
    assert engine._host_rate_limit("api.example.com", 5) == 5
    assert engine._host_rate_limit("other.example.com", None) is None
    assert engine._host_rate_limit("other.example.com", "12") == 12


def test_rate_limit_admit_waits_when_window_full(monkeypatch):
    engine._rate_hits.clear()

    async def _drive():
        w1 = await engine._rate_limit_admit("h.test", 2)
        w2 = await engine._rate_limit_admit("h.test", 2)
        # Fill the window artificially so the third admit must wait ~0.2s.
        now = time.monotonic()
        engine._rate_hits["h.test"] = [now - 59.8, now - 59.8]
        w3 = await engine._rate_limit_admit("h.test", 2)
        return w1, w2, w3

    w1, w2, w3 = asyncio.run(_drive())
    assert w1 == 0.0 and w2 == 0.0
    assert w3 > 0.0
    engine._rate_hits.clear()


def test_http_request_reports_rate_limited_wait(monkeypatch):
    engine._rate_hits.clear()
    monkeypatch.setattr(engine, "_global_rate_limits", {})

    class FakeResponse:
        status_code = 200
        is_success = True
        headers = {"content-type": "application/json"}
        text = '{"ok": true}'

        def json(self):
            return {"ok": True}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, **kwargs):
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    now = time.monotonic()
    engine._rate_hits["api.fake.test"] = [now - 59.9]
    out = asyncio.run(engine._exec_http_request(
        {"url": "https://api.fake.test/x", "maxRequestsPerMinute": 1}
    ))
    assert out["status"] == 200
    assert out.get("rate_limited_s", 0) > 0
    engine._rate_hits.clear()
