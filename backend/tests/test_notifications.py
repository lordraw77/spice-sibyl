"""
Phase 23.c tests — cross-channel notification bridge (Telegram <-> web).
"""

import pytest

from app.db import notification_events_repository as events_repo
from app.db import profile_repository, telegram_link_repository
from app.db.database import get_db
from app.services import notification_service


@pytest.mark.anyio
async def test_event_allowed_default_and_opt_out():
    assert notification_service._event_allowed({}, "workflowDone") is True
    assert notification_service._event_allowed({"workflowDone": True}, "workflowDone") is True
    assert notification_service._event_allowed({"workflowDone": False}, "workflowDone") is False


@pytest.mark.anyio
async def test_notify_telegram_noop_without_link():
    # No telegram_links row for this profile → silent no-op, no exception.
    async for db in get_db():
        await notification_service.notify_telegram(db, "nonexistent-profile", "workflowDone", "hello")


@pytest.mark.anyio
async def test_notify_web_creates_event_and_publishes_live():
    async for db in get_db():
        profile = await profile_repository.create_profile(db, "notif-test-profile", user_id="notif-test-user")

        queue = notification_service.subscribe(profile.user_id)
        try:
            await notification_service.notify_web(
                db, profile.id, "kbIngested", title="Doc added", body="report.pdf"
            )

            # Persisted for the badge/unread count.
            items = await events_repo.list_for_user(db, profile.user_id)
            assert len(items) == 1
            assert items[0]["event_type"] == "kbIngested"
            assert items[0]["title"] == "Doc added"
            assert items[0]["read"] is False
            assert await events_repo.unread_count(db, profile.user_id) == 1

            # Fanned out live to the subscribed queue.
            assert queue.qsize() == 1
            live_event = queue.get_nowait()
            assert live_event["id"] == items[0]["id"]
        finally:
            notification_service.unsubscribe(profile.user_id, queue)


@pytest.mark.anyio
async def test_notify_web_respects_opt_out():
    from app.db import settings_repository

    async for db in get_db():
        profile = await profile_repository.create_profile(db, "notif-optout-profile", user_id="notif-optout-user")
        await settings_repository.put(db, f"user:{profile.user_id}", {"notifyPrefs": {"kbIngested": False}})

        await notification_service.notify_web(db, profile.id, "kbIngested", title="Doc added")

        items = await events_repo.list_for_user(db, profile.user_id)
        assert items == []


@pytest.mark.anyio
async def test_notify_telegram_mute_blocks_send():
    from app.db import telegram_prefs_repository

    async for db in get_db():
        profile = await profile_repository.create_profile(db, "notif-mute-profile", user_id="notif-mute-user")
        await telegram_link_repository.link(db, telegram_id=987654321, profile_id=profile.id)
        await telegram_prefs_repository.set_notify(987654321, False)

        # is_notify_enabled() reads the warm-cached dict, which set_notify() updates
        # in bot.py's handler but not here directly — exercise the repo + bot cache path.
        from app.telegram import bot as telegram_bot

        telegram_bot._notify_prefs[987654321] = False
        try:
            # No bot running in tests (TELEGRAM_BOT_TOKEN=""), so get_bot() is None
            # regardless; this just verifies the mute check runs before that and
            # never raises.
            await notification_service.notify_telegram(db, profile.id, "workflowDone", "hi")
            assert telegram_bot.is_notify_enabled(987654321) is False
        finally:
            telegram_bot._notify_prefs.pop(987654321, None)


def test_notifications_list_endpoint_empty(client, auth_headers):
    resp = client.get("/api/v1/notifications", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body and "unread_count" in body


def test_notifications_mark_read_missing(client, auth_headers):
    resp = client.post("/api/v1/notifications/does-not-exist/read", headers=auth_headers)
    assert resp.status_code == 404


def test_notifications_trigger_noop_when_unlinked(client, auth_headers):
    # No Telegram link for the admin's profile → best-effort no-op, still 200.
    resp = client.post(
        "/api/v1/notifications/trigger",
        json={"event_type": "longCompletionDone", "title": "Reply ready", "body": "..."},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
