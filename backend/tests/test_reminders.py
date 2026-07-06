"""
Phase 23.d tests — cross-channel reminders (recurrence, NL parsing, CRUD API,
snooze/repeat, smart tool-loop).
"""

from zoneinfo import ZoneInfo

import pytest

from app.db import profile_repository, reminder_repository as reminder_repo
from app.db.database import get_db
from app.services import reminder_parsing, reminder_service

_TZ = ZoneInfo("UTC")


# ── NL / recurrence parsing ───────────────────────────────────────────────────

def test_parse_relative_and_absolute():
    assert reminder_parsing.parse_recurrence_and_when("+30m Check backups", _TZ)[0] == "once"
    assert reminder_parsing.parse_recurrence_and_when("15:50 Call Mario", _TZ)[0] == "once"
    parsed = reminder_parsing.parse_recurrence_and_when("2h Meeting", _TZ)
    assert parsed[2] == "Meeting"


def test_parse_every_day():
    recurrence, fire_at, text = reminder_parsing.parse_recurrence_and_when(
        "every day 08:00 Take vitamins", _TZ
    )
    assert recurrence == "daily"
    assert text == "Take vitamins"
    assert fire_at > 0


def test_parse_every_weekday():
    recurrence, _, text = reminder_parsing.parse_recurrence_and_when(
        "every monday Weekly meeting", _TZ
    )
    assert recurrence == "weekly:mon"
    assert text == "Weekly meeting"


def test_parse_cron():
    recurrence, fire_at, text = reminder_parsing.parse_recurrence_and_when(
        "cron:0,8,*,*,1-5 Weekday alarm", _TZ
    )
    assert recurrence == "cron:0,8,*,*,1-5"
    assert text == "Weekday alarm"
    assert fire_at > 0


def test_parse_natural_language_phrases():
    assert reminder_parsing.parse_recurrence_and_when("domani alle 9 Dentista", _TZ)[2] == "Dentista"
    assert reminder_parsing.parse_recurrence_and_when("tomorrow at 9 Dentist", _TZ)[2] == "Dentist"
    assert reminder_parsing.parse_recurrence_and_when("tra due ore Riunione", _TZ)[2] == "Riunione"
    assert reminder_parsing.parse_recurrence_and_when("in two hours Meeting", _TZ)[2] == "Meeting"
    assert reminder_parsing.parse_recurrence_and_when("fra 30 min  bere", _TZ)[2] == "bere"
    assert reminder_parsing.parse_recurrence_and_when("il 15 alle 14:30 Visita", _TZ)[2] == "Visita"
    assert reminder_parsing.parse_recurrence_and_when("stasera Guarda un film", _TZ)[2] == "Guarda un film"


def test_parse_unrecognized_returns_none():
    assert reminder_parsing.parse_recurrence_and_when("blah blah blah", _TZ) is None


def test_compute_next_fire_once_is_none():
    assert reminder_parsing.compute_next_fire("once", 1_700_000_000, _TZ) is None


def test_compute_next_fire_daily():
    after = 1_700_000_000
    nxt = reminder_parsing.compute_next_fire("daily", after, _TZ)
    assert nxt == after + 86400


def test_compute_next_fire_cron():
    after = 1_700_000_000
    nxt = reminder_parsing.compute_next_fire("cron:0,8,*,*,*", after, _TZ)
    assert nxt is not None and nxt > after


# ── Repository + service CRUD ─────────────────────────────────────────────────

@pytest.mark.anyio
async def test_reminder_repository_crud():
    reminder_id = await reminder_repo.create(
        owner_profile_id="rem-test-profile", chat_id=None, text="hello",
        smart_prompt=None, recurrence="once", fire_at=9_999_999_999,
        timezone=None, channels="web",
    )
    row = await reminder_repo.get(reminder_id)
    assert row["text"] == "hello"
    assert row["active"] == 1

    await reminder_repo.update(reminder_id, active=0)
    row = await reminder_repo.get(reminder_id)
    assert row["active"] == 0

    rows = await reminder_repo.list_for_profile("rem-test-profile")
    assert any(r["id"] == reminder_id for r in rows)

    assert await reminder_repo.delete(reminder_id, owner_profile_id="rem-test-profile") is True
    assert await reminder_repo.get(reminder_id) is None


@pytest.mark.anyio
async def test_delete_scoped_to_chat_id():
    reminder_id = await reminder_repo.create(
        owner_profile_id=None, chat_id=12345, text="chat-scoped", smart_prompt=None,
        recurrence="once", fire_at=9_999_999_999, timezone=None, channels="telegram",
    )
    assert await reminder_service.delete(reminder_id, chat_id=99999) is False
    assert await reminder_repo.get(reminder_id) is not None
    assert await reminder_service.delete(reminder_id, chat_id=12345) is True
    assert await reminder_repo.get(reminder_id) is None


@pytest.mark.anyio
async def test_list_all_active_due():
    past = await reminder_repo.create(
        owner_profile_id="due-profile", chat_id=None, text="due", smart_prompt=None,
        recurrence="once", fire_at=1, timezone=None, channels="web",
    )
    future = await reminder_repo.create(
        owner_profile_id="due-profile", chat_id=None, text="future", smart_prompt=None,
        recurrence="once", fire_at=9_999_999_999, timezone=None, channels="web",
    )
    due = await reminder_repo.list_all_active_due(1_000_000_000)
    ids = {r["id"] for r in due}
    assert past in ids
    assert future not in ids


# ── Snooze / repeat / fire ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_snooze_reschedules():
    reminder_id = await reminder_repo.create(
        owner_profile_id="snooze-profile", chat_id=None, text="snoozeme", smart_prompt=None,
        recurrence="once", fire_at=1, timezone=None, channels="web",
    )
    assert await reminder_service.snooze(reminder_id, minutes=5) is True
    row = await reminder_repo.get(reminder_id)
    assert row["fire_at"] > 1
    assert row["fired"] == 0


@pytest.mark.anyio
async def test_fire_once_marks_fired_and_delivers():
    async for db in get_db():
        profile = await profile_repository.create_profile(db, "fire-once-profile", user_id="fire-once-user")

    reminder_id = await reminder_repo.create(
        owner_profile_id=profile.id, chat_id=None, text="one-shot", smart_prompt=None,
        recurrence="once", fire_at=1, timezone=None, channels="web",
    )
    await reminder_service.fire(reminder_id)
    row = await reminder_repo.get(reminder_id)
    assert row["fired"] == 1
    assert row["last_fired_at"] is not None


@pytest.mark.anyio
async def test_fire_recurring_reschedules_without_marking_fired():
    async for db in get_db():
        profile = await profile_repository.create_profile(db, "fire-daily-profile", user_id="fire-daily-user")

    reminder_id = await reminder_repo.create(
        owner_profile_id=profile.id, chat_id=None, text="daily-text", smart_prompt=None,
        recurrence="daily", fire_at=1, timezone=None, channels="web",
    )
    await reminder_service.fire(reminder_id)
    row = await reminder_repo.get(reminder_id)
    assert row["fired"] == 0
    assert row["fire_at"] == 1 + 86400


@pytest.mark.anyio
async def test_repeat_redelivers_without_touching_schedule():
    async for db in get_db():
        profile = await profile_repository.create_profile(db, "repeat-profile", user_id="repeat-user")

    reminder_id = await reminder_repo.create(
        owner_profile_id=profile.id, chat_id=None, text="repeatable", smart_prompt=None,
        recurrence="once", fire_at=9_999_999_999, timezone=None, channels="web",
    )
    assert await reminder_service.repeat(reminder_id) is True
    row = await reminder_repo.get(reminder_id)
    assert row["fire_at"] == 9_999_999_999
    assert row["fired"] == 0


# ── API endpoints ──────────────────────────────────────────────────────────────

def test_reminders_list_empty(client, auth_headers):
    resp = client.get("/api/v1/reminders", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_reminders_create_edit_snooze_delete(client, auth_headers):
    resp = client.post(
        "/api/v1/reminders",
        json={"text": "Water the plants", "recurrence": "once", "fire_at": 9_999_999_999, "channels": "web"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    reminder = resp.json()
    assert reminder["text"] == "Water the plants"
    assert reminder["active"] is True

    reminder_id = reminder["id"]

    resp = client.patch(
        f"/api/v1/reminders/{reminder_id}", json={"active": False}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] is False

    resp = client.post(
        f"/api/v1/reminders/{reminder_id}/snooze", json={"minutes": 15}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] is True  # snooze reactivates

    resp = client.post(f"/api/v1/reminders/{reminder_id}/repeat", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == reminder_id  # repeat doesn't touch the schedule

    resp = client.delete(f"/api/v1/reminders/{reminder_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    resp = client.get("/api/v1/reminders", headers=auth_headers)
    assert all(r["id"] != reminder_id for r in resp.json())


def test_reminders_create_requires_text_or_prompt(client, auth_headers):
    resp = client.post(
        "/api/v1/reminders", json={"recurrence": "once", "fire_at": 1}, headers=auth_headers
    )
    assert resp.status_code == 400


def test_reminders_patch_not_owned_returns_404(client, auth_headers):
    resp = client.patch(
        "/api/v1/reminders/does-not-exist", json={"active": False}, headers=auth_headers
    )
    assert resp.status_code == 404


# ── Smart reminder tool loop (mocked provider) ────────────────────────────────

@pytest.mark.anyio
async def test_llm_parse_fallback_used_when_regex_fails(monkeypatch):
    assert reminder_parsing.parse_recurrence_and_when("blah blah blah", _TZ) is None

    class _FakeProvider:
        async def complete(self, request):
            return {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {
                        "content": (
                            '{"recurrence": "once", '
                            '"fire_at": "2030-01-01T09:00:00+00:00", "text": "bere"}'
                        ),
                    },
                }]
            }

    from app.services import provider_factory

    monkeypatch.setattr(provider_factory.ProviderFactory, "get_provider", lambda model: _FakeProvider())

    parsed = await reminder_service._llm_parse_fallback("blah blah blah", _TZ)
    assert parsed == ("once", 1893488400, "bere")


@pytest.mark.anyio
async def test_llm_parse_fallback_returns_none_on_bad_json(monkeypatch):
    class _FakeProvider:
        async def complete(self, request):
            return {"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]}

    from app.services import provider_factory

    monkeypatch.setattr(provider_factory.ProviderFactory, "get_provider", lambda model: _FakeProvider())

    assert await reminder_service._llm_parse_fallback("blah blah blah", _TZ) is None


@pytest.mark.anyio
async def test_smart_prompt_returns_final_answer_without_tool_calls(monkeypatch):
    class _FakeProvider:
        async def complete(self, request):
            return {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": "Here is your summary.", "tool_calls": []},
                }]
            }

    from app.services import provider_factory

    monkeypatch.setattr(provider_factory.ProviderFactory, "get_provider", lambda model: _FakeProvider())

    result = await reminder_service._run_smart_prompt("Summarize my feeds", "default")
    assert result == "Here is your summary."
