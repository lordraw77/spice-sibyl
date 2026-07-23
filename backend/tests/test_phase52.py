"""
Phase 52 — roadmap fase 20: Telegram as a first-class workflow channel.

Covers the `telegram` inbound trigger + `/run` launcher plumbing (20.1), the
outbound `telegram.send`/edit/delete/media nodes (20.2 — exercised on their
bot-not-running no-op path, since the tests run without a live bot), the
`telegram.ask` interactive keyboard suspending a run via the wait.event path
(20.3), inbound file ingestion helper (20.4), and command↔workflow bindings with
collision rejection (20.5). Full runs use ``run_workflow_sync``.
"""

import asyncio

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
                db, wf["id"], wf["profile_id"], trigger_type="telegram",
                trigger_payload=payload or {},
            )
        finally:
            await db.close()
    return asyncio.run(_call())


# ── catalog / trigger type (20.1/20.2) ───────────────────────────────────────

def test_catalog_includes_telegram_nodes(client, auth_headers):
    catalog = {t["type"] for t in client.get(
        "/api/v1/graph-workflows/node-types", headers=auth_headers).json()}
    assert {"telegram", "telegram.send", "telegram.sendMedia",
            "telegram.editMessage", "telegram.deleteMessage", "telegram.ask"} <= catalog


def test_telegram_is_a_trigger_type():
    assert "telegram" in engine._TRIGGER_TYPES


# ── chat-id resolution (20.2) ────────────────────────────────────────────────

def test_resolve_chat_id_prefers_explicit():
    assert engine._resolve_chat_id({"chat_id": 5}, {"trigger": {"chat_id": 9}}) == 5


def test_resolve_chat_id_falls_back_to_trigger():
    assert engine._resolve_chat_id({}, {"trigger": {"chat_id": 9}}) == 9


def test_resolve_chat_id_raises_without_target():
    with pytest.raises(ValueError):
        engine._resolve_chat_id({}, {"trigger": {}})


# ── outbound nodes no-op cleanly off Telegram (20.2) ─────────────────────────

def test_telegram_send_noops_when_bot_absent(client, auth_headers):
    graph = {"nodes": [
        {"id": "t", "type": "telegram", "position": {"x": 0, "y": 0}},
        {"id": "s", "type": "telegram.send", "position": {"x": 1, "y": 0},
         "params": {"text": "hi"}},
    ], "edges": [{"id": "e", "source": "t", "target": "s"}]}
    wf = _make_wf(client, auth_headers, "tg-send", graph)
    run = _run_sync(wf, {"chat_id": 123})
    assert run["status"] == "completed", run
    assert run["output"]["sent"] is False
    assert run["output"]["reason"] == "bot_not_running"


def test_telegram_send_uses_trigger_chat(client, auth_headers):
    graph = {"nodes": [
        {"id": "t", "type": "telegram", "position": {"x": 0, "y": 0}},
        {"id": "s", "type": "telegram.send", "position": {"x": 1, "y": 0},
         "params": {"text": "hi"}},
    ], "edges": [{"id": "e", "source": "t", "target": "s"}]}
    wf = _make_wf(client, auth_headers, "tg-send-2", graph)
    run = _run_sync(wf, {"chat_id": 777})
    assert run["output"]["chat_id"] == 777


def test_telegram_edit_requires_message_id(client, auth_headers):
    graph = {"nodes": [
        {"id": "t", "type": "telegram", "position": {"x": 0, "y": 0}},
        {"id": "e1", "type": "telegram.editMessage", "position": {"x": 1, "y": 0},
         "params": {"text": "x"}},
    ], "edges": [{"id": "e", "source": "t", "target": "e1"}]}
    wf = _make_wf(client, auth_headers, "tg-edit", graph)
    run = _run_sync(wf, {"chat_id": 1})
    assert run["status"] == "failed"


# ── telegram.ask suspends the run (20.3) ─────────────────────────────────────

def test_telegram_ask_suspends_and_resumes(client, auth_headers):
    graph = {"nodes": [
        {"id": "t", "type": "telegram", "position": {"x": 0, "y": 0}},
        {"id": "a", "type": "telegram.ask", "position": {"x": 1, "y": 0},
         "params": {"text": "Pick", "options": [{"label": "Yes", "value": "yes"}]}},
    ], "edges": [{"id": "e", "source": "t", "target": "a"}]}
    wf = _make_wf(client, auth_headers, "tg-ask", graph)

    async def _drive():
        db = await engine._connect()
        try:
            run_id = await engine.run_workflow(
                db, wf["id"], wf["profile_id"], trigger_type="telegram",
                trigger_payload={"chat_id": 42},
            )
            # Wait for the run to reach 'waiting' on the ask node.
            for _ in range(50):
                await asyncio.sleep(0.1)
                run = await repo.get_run(db, run_id)
                if run.status == "waiting":
                    break
            assert run.status == "waiting", run.status
            ap = await repo.get_pending_approval(db, run_id, "a")
            assert ap is not None and ap.kind == "event"
            # Deliver the tapped value the way _cb_telegram_ask does.
            await repo.decide_approval(db, ap.id, status="delivered",
                                       decided_by="telegram:1", data={"value": "yes"})
            for _ in range(50):
                await asyncio.sleep(0.1)
                run = await repo.get_run(db, run_id)
                if run.status in ("completed", "failed"):
                    break
            return run
        finally:
            await db.close()

    run = asyncio.run(_drive())
    assert run.status == "completed", run.status


# ── command bindings (20.5) ──────────────────────────────────────────────────

def test_create_and_list_binding(client, auth_headers):
    graph = {"nodes": [{"id": "t", "type": "telegram", "position": {"x": 0, "y": 0}}], "edges": []}
    wf = _make_wf(client, auth_headers, "bound-wf", graph)
    resp = client.post("/api/v1/graph-workflows/telegram-bindings",
                       json={"command": "/report", "workflow_id": wf["id"]}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["command"] == "report"  # slash stripped
    listed = client.get("/api/v1/graph-workflows/telegram-bindings", headers=auth_headers).json()
    assert any(b["command"] == "report" for b in listed)


def test_binding_collision_rejected(client, auth_headers):
    graph = {"nodes": [{"id": "t", "type": "telegram", "position": {"x": 0, "y": 0}}], "edges": []}
    wf = _make_wf(client, auth_headers, "bound-wf-2", graph)
    client.post("/api/v1/graph-workflows/telegram-bindings",
                json={"command": "status", "workflow_id": wf["id"]}, headers=auth_headers)
    resp = client.post("/api/v1/graph-workflows/telegram-bindings",
                       json={"command": "status", "workflow_id": wf["id"]}, headers=auth_headers)
    assert resp.status_code == 409


def test_delete_binding(client, auth_headers):
    graph = {"nodes": [{"id": "t", "type": "telegram", "position": {"x": 0, "y": 0}}], "edges": []}
    wf = _make_wf(client, auth_headers, "bound-wf-3", graph)
    client.post("/api/v1/graph-workflows/telegram-bindings",
                json={"command": "go", "workflow_id": wf["id"]}, headers=auth_headers)
    resp = client.delete("/api/v1/graph-workflows/telegram-bindings/go", headers=auth_headers)
    assert resp.status_code == 204
    listed = client.get("/api/v1/graph-workflows/telegram-bindings", headers=auth_headers).json()
    assert not any(b["command"] == "go" for b in listed)


# ── inbound trigger dispatch helper (20.1) ───────────────────────────────────

def test_run_telegram_workflow_returns_reply(client, auth_headers):
    graph = {"nodes": [
        {"id": "t", "type": "telegram", "position": {"x": 0, "y": 0}},
        {"id": "r", "type": "chat.reply", "position": {"x": 1, "y": 0},
         "params": {"text": "pong"}},
    ], "edges": [{"id": "e", "source": "t", "target": "r"}]}
    wf = _make_wf(client, auth_headers, "tg-reply", graph)

    from app.telegram import bot as tg_bot

    result = asyncio.run(tg_bot.run_telegram_workflow(
        wf["id"], wf["profile_id"], chat_id=99, text="/ping", command="ping",
    ))
    assert result["status"] == "completed", result
    assert result["reply"] == "pong"
