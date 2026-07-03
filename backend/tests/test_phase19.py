"""
Phase 19 tests — persistent memory, feedback, response cache, new built-in tools.
"""

import time

import pytest

from app.schemas.chat import ChatCompletionRequest, ChatMessage
from app.schemas.memories import MemoryOut
from app.services import cache_service, memory_service
from app.tools import extras
from app.tools.registry import TOOL_DEFINITIONS


# ── Memory endpoints ─────────────────────────────────────────────────────────

def test_memory_crud(client, auth_headers):
    created = client.post(
        "/api/v1/memories",
        json={"content": "Preferisce risposte concise", "category": "preference"},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    mem = created.json()
    assert mem["category"] == "preference"
    assert mem["enabled"] is True

    listed = client.get("/api/v1/memories", headers=auth_headers)
    assert listed.status_code == 200
    assert any(m["id"] == mem["id"] for m in listed.json())

    updated = client.patch(
        f"/api/v1/memories/{mem['id']}",
        json={"content": "Preferisce l'italiano", "enabled": False},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["content"] == "Preferisce l'italiano"
    assert updated.json()["enabled"] is False

    deleted = client.delete(f"/api/v1/memories/{mem['id']}", headers=auth_headers)
    assert deleted.status_code == 204

    missing = client.patch(
        f"/api/v1/memories/{mem['id']}", json={"enabled": True}, headers=auth_headers
    )
    assert missing.status_code == 404


def test_memory_settings_toggle(client, auth_headers):
    initial = client.get("/api/v1/memories/settings", headers=auth_headers)
    assert initial.status_code == 200
    assert initial.json()["memory_enabled"] is True

    off = client.put(
        "/api/v1/memories/settings", json={"memory_enabled": False}, headers=auth_headers
    )
    assert off.status_code == 200
    assert off.json()["memory_enabled"] is False

    # restore
    client.put("/api/v1/memories/settings", json={"memory_enabled": True}, headers=auth_headers)


def test_memory_forget_all(client, auth_headers):
    client.post("/api/v1/memories", json={"content": "fatto uno"}, headers=auth_headers)
    client.post("/api/v1/memories", json={"content": "fatto due"}, headers=auth_headers)
    resp = client.delete("/api/v1/memories", headers=auth_headers)
    assert resp.status_code == 204
    assert client.get("/api/v1/memories", headers=auth_headers).json() == []


# ── Memory service unit tests ────────────────────────────────────────────────

def _mem(content: str, category: str = "fact") -> MemoryOut:
    now = int(time.time())
    return MemoryOut(
        id="m1", profile_id="p", content=content, category=category,
        enabled=True, created_at=now, updated_at=now,
    )


def test_build_memory_block_budget():
    memories = [_mem(f"fatto numero {i} " + "x" * 50) for i in range(100)]
    block = memory_service.build_memory_block(memories, max_chars=300)
    assert block.startswith("<user_memory>")
    assert len(block) < 500  # budget + envelope


def test_build_memory_block_empty():
    assert memory_service.build_memory_block([]) == ""


def test_parse_operations_tolerates_fences():
    raw = '```json\n[{"op": "add", "content": "usa Python", "category": "fact"}]\n```'
    ops = memory_service._parse_operations(raw)
    assert ops == [{"op": "add", "content": "usa Python", "category": "fact"}]


def test_parse_operations_garbage():
    assert memory_service._parse_operations("nessuna operazione qui") == []


def test_apply_memory_block_appends_to_system():
    req = ChatCompletionRequest(
        model="mock/m",
        messages=[ChatMessage(role="system", content="Sei un assistente."),
                  ChatMessage(role="user", content="ciao")],
    )
    out = memory_service.apply_memory_block(req, "<user_memory>\n- x\n</user_memory>")
    assert out.messages[0].role == "system"
    assert "<user_memory>" in out.messages[0].content
    assert "Sei un assistente." in out.messages[0].content


def test_apply_memory_block_prepends_when_no_system():
    req = ChatCompletionRequest(
        model="mock/m", messages=[ChatMessage(role="user", content="ciao")]
    )
    out = memory_service.apply_memory_block(req, "<user_memory>\n- x\n</user_memory>")
    assert out.messages[0].role == "system"
    assert len(out.messages) == 2


# ── Feedback endpoints ───────────────────────────────────────────────────────

def _make_conversation_with_exchange(client, auth_headers) -> tuple[str, str, str]:
    import uuid

    user_id, assistant_id = f"u-{uuid.uuid4()}", f"a-{uuid.uuid4()}"
    conv = client.post(
        "/api/v1/conversations",
        json={"title": "test feedback", "model": "mock/model"},
        headers=auth_headers,
    ).json()
    client.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={
            "memory": False,  # keep background extraction out of the test
            "messages": [
                {"id": user_id, "role": "user", "content": "domanda?"},
                {"id": assistant_id, "role": "assistant", "content": "risposta."},
            ],
        },
        headers=auth_headers,
    )
    return conv["id"], user_id, assistant_id


def test_feedback_set_and_clear(client, auth_headers):
    _conv_id, _user_id, msg_id = _make_conversation_with_exchange(client, auth_headers)

    up = client.put(
        f"/api/v1/feedback/messages/{msg_id}",
        json={"rating": 1},
        headers=auth_headers,
    )
    assert up.status_code == 200, up.text
    assert up.json()["rating"] == 1

    down = client.put(
        f"/api/v1/feedback/messages/{msg_id}",
        json={"rating": -1, "note": "troppo vaga"},
        headers=auth_headers,
    )
    assert down.status_code == 200
    assert down.json()["note"] == "troppo vaga"

    stats = client.get("/api/v1/feedback/stats", headers=auth_headers).json()
    assert stats["down"] >= 1

    export = client.get("/api/v1/feedback/export", headers=auth_headers)
    assert export.status_code == 200
    dataset = export.json()
    row = next(r for r in dataset if r["message_id"] == msg_id)
    assert row["rating"] == -1
    assert row["prompt"] == "domanda?"
    assert row["response"] == "risposta."

    cleared = client.delete(f"/api/v1/feedback/messages/{msg_id}", headers=auth_headers)
    assert cleared.status_code == 204


def test_feedback_rejects_user_message(client, auth_headers):
    _conv_id, user_id, _ = _make_conversation_with_exchange(client, auth_headers)
    resp = client.put(
        f"/api/v1/feedback/messages/{user_id}", json={"rating": 1}, headers=auth_headers
    )
    assert resp.status_code == 422


# ── Response cache ───────────────────────────────────────────────────────────

def test_cache_roundtrip():
    cache_service.clear()
    req = ChatCompletionRequest(
        model="mock/m", messages=[ChatMessage(role="user", content="2+2?")],
        temperature=0.0,
    )
    key = cache_service.cache_key(req)
    assert key is not None
    assert cache_service.get(key) is None
    cache_service.put(key, "4", {"usage": {"total_tokens": 3}})
    hit = cache_service.get(key)
    assert hit is not None and hit["content"] == "4"

    # A different prompt misses.
    other = req.model_copy(update={"messages": [ChatMessage(role="user", content="3+3?")]})
    assert cache_service.get(cache_service.cache_key(other)) is None
    cache_service.clear()


def test_cache_skips_tools_and_agents():
    req_tools = ChatCompletionRequest(
        model="mock/m",
        messages=[ChatMessage(role="user", content="x")],
        tools=[{"type": "function", "function": {"name": "t", "description": "d"}}],
    )
    assert cache_service.cache_key(req_tools) is None
    req_agent = ChatCompletionRequest(
        model="agent/multi-mcp", messages=[ChatMessage(role="user", content="x")]
    )
    assert cache_service.cache_key(req_agent) is None


# ── Service info endpoint ────────────────────────────────────────────────────

def test_info_endpoint(client, auth_headers):
    resp = client.get("/api/v1/info", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] and data["version"]
    assert data["api_base"] == "/api/v1"
    assert data["uptime_seconds"] >= 0
    assert isinstance(data["providers_configured"], int)
    assert "memory" in data["features"] and "response_cache" in data["features"]
    assert "entries" in data["response_cache"]


def test_info_requires_auth(client):
    assert client.get("/api/v1/info").status_code == 401


# ── New built-in tools ───────────────────────────────────────────────────────

def test_new_tools_registered():
    names = {d["function"]["name"] for d in TOOL_DEFINITIONS}
    for expected in (
        "kb_search", "search_conversations", "generate_image", "get_weather",
        "fetch_rss", "create_reminder", "extract_document", "http_request",
    ):
        assert expected in names


def test_tools_endpoint_lists_new_tools(client, auth_headers):
    resp = client.get("/api/v1/tools", headers=auth_headers)
    assert resp.status_code == 200
    names = {d["function"]["name"] for d in resp.json()}
    assert "kb_search" in names and "http_request" in names


def test_ssrf_guard_blocks_private_hosts():
    assert extras.assert_public_url("http://127.0.0.1/x") is not None
    assert extras.assert_public_url("http://localhost:8000/x") is not None
    assert extras.assert_public_url("http://192.168.1.10/x") is not None
    assert extras.assert_public_url("ftp://example.com/x") is not None


def test_parse_when_relative_and_absolute():
    now = int(time.time())
    fire = extras._parse_when("+30m")
    assert fire is not None and 29 * 60 <= fire - now <= 31 * 60
    assert extras._parse_when("25:99") is None
    assert extras._parse_when("domani") is None
    hm = extras._parse_when("23:59")
    assert hm is not None and hm > now


@pytest.mark.anyio
async def test_search_conversations_tool_empty_profile():
    result = await extras.search_conversations("qualcosa", profile_id="nonexistent-profile")
    assert "No past conversations" in result or "Error" not in result


def test_create_reminder_requires_bot_configured():
    import asyncio
    result = asyncio.new_event_loop().run_until_complete(
        extras.create_reminder("test", "+30m", profile_id="p")
    )
    # In tests TELEGRAM_BOT_TOKEN is empty → clear error message, no crash.
    assert result.startswith("Error:")
