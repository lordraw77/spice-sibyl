"""
notification_service — cross-channel notification bridge (Phase 23.c).

Two directions, both gated by the linked profile and per-event-type opt-in:

  * notify_telegram — web → Telegram. Looks up the Telegram chat linked to a
    profile, checks the owning user's ``notifyPrefs`` (roaming settings blob,
    see app.db.settings_repository) and the per-chat mute (/notify off), then
    pushes a message via the live bot instance (app.telegram.bot.get_bot()).
  * notify_web — Telegram → web. Persists a notification_events row and fans
    it out live to any open SSE stream for the owning user (see
    app/api/v1/endpoints/notifications.py).

Both functions are best-effort: a missing link, an opted-out event type, or an
inactive bot/stream is a silent no-op, never an error surfaced to the caller.
"""

import asyncio
import logging
from collections import defaultdict

import aiosqlite

from app.db import notification_events_repository, profile_repository, settings_repository
from app.db import telegram_link_repository

logger = logging.getLogger(__name__)

# user_id → list of live SSE subscriber queues (Phase 23.c web-side stream).
_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)


def subscribe(user_id: str) -> asyncio.Queue:
    """Register a new SSE listener for user_id; call unsubscribe() when the stream closes."""
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers[user_id].append(queue)
    return queue


def unsubscribe(user_id: str, queue: asyncio.Queue) -> None:
    listeners = _subscribers.get(user_id)
    if not listeners:
        return
    try:
        listeners.remove(queue)
    except ValueError:
        pass
    if not listeners:
        _subscribers.pop(user_id, None)


def _publish(user_id: str, event: dict) -> None:
    for queue in _subscribers.get(user_id, []):
        queue.put_nowait(event)


async def _notify_prefs_for_user(db: aiosqlite.Connection, user_id: str) -> dict:
    blob = await settings_repository.get(db, f"user:{user_id}")
    prefs = blob.get("notifyPrefs")
    return prefs if isinstance(prefs, dict) else {}


def _event_allowed(prefs: dict, event_type: str) -> bool:
    """Opt-out model: an event type is enabled unless explicitly set to False."""
    return prefs.get(event_type) is not False


async def notify_telegram(db: aiosqlite.Connection, profile_id: str, event_type: str, text: str) -> None:
    """Push a Telegram message to the profile's linked chat, if opted in."""
    link = await telegram_link_repository.get_by_profile_id(db, profile_id)
    if not link:
        return

    profile = await profile_repository.get_profile(db, profile_id)
    if profile and profile.user_id:
        prefs = await _notify_prefs_for_user(db, profile.user_id)
        if not _event_allowed(prefs, event_type):
            return

    from app.telegram import bot as telegram_bot

    chat_id = link["telegram_id"]
    if not telegram_bot.is_notify_enabled(chat_id):
        return

    bot = telegram_bot.get_bot()
    if bot is None:
        logger.debug("notify_telegram: bot not running, dropping event=%s profile=%s", event_type, profile_id)
        return

    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        logger.exception("notify_telegram: send failed event=%s chat_id=%s", event_type, chat_id)


async def notify_web(
    db: aiosqlite.Connection,
    profile_id: str,
    event_type: str,
    title: str,
    body: str = "",
    meta: dict | None = None,
) -> None:
    """Record a Telegram-originated event and push it live to any open web stream."""
    profile = await profile_repository.get_profile(db, profile_id)
    if not profile or not profile.user_id:
        return

    prefs = await _notify_prefs_for_user(db, profile.user_id)
    if not _event_allowed(prefs, event_type):
        return

    event = await notification_events_repository.create(
        db, profile.user_id, profile_id, event_type, title, body, meta
    )
    _publish(profile.user_id, event)
