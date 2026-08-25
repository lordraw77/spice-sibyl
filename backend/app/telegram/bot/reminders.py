"""/remind, /remindai, /reminders, /unremind and their callbacks.

Extracted from the former single-file bot.py.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.core.config import settings
from app.db import reminder_repository as reminder_repo
from app.services import reminder_service
from app.telegram.i18n import t

from .account import _linked_profile_id
from .state import _is_allowed, _locale

logger = logging.getLogger(__name__)

# ── Reminders (/remind, /remindai) — Phase 23.d ───────────────────────────────
#
# Firing is owned by app.services.reminder_service's channel-agnostic polling
# loop (started in app/main.py's lifespan), not the Telegram JobQueue — a
# reminder fires whether or not the bot is running, and web-only reminders
# (no linked Telegram chat) work the same way.


def _tz() -> ZoneInfo:
    """Configured display/parsing timezone (independent of the container TZ)."""
    try:
        return ZoneInfo(settings.timezone)
    except Exception:
        logger.warning("Timezone %r non valida, uso UTC", settings.timezone)
        return ZoneInfo("UTC")


def _fmt_when(fire_at: int, locale: str | None = None) -> str:
    """Format a reminder time in the chat's timezone, with locale-aware date
    order (Phase 22.c): en uses month/day, the others day/month."""
    dt = datetime.fromtimestamp(fire_at, _tz())
    fmt = "%m/%d %H:%M" if locale == "en" else "%d/%m %H:%M"
    return dt.strftime(fmt)


async def _do_remind(update: Update, context: ContextTypes.DEFAULT_TYPE, *, smart: bool) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("_do_remind: accesso negato user_id=%s", user.id)
        return
    chat_id = update.effective_chat.id
    loc = _locale(chat_id)

    usage_key = "remindai_usage" if smart else "remind_usage"
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(t(loc, usage_key), parse_mode=ParseMode.HTML)
        return

    raw = " ".join(context.args)
    profile_id = await _linked_profile_id(chat_id)
    created = await reminder_service.create_from_text(
        raw, owner_profile_id=profile_id, chat_id=chat_id, channels="telegram",
        tz_name=settings.timezone, smart=smart,
    )
    if created is None:
        await update.message.reply_text(t(loc, "remind_invalid_time"), parse_mode=ParseMode.HTML)
        return

    when_str = _fmt_when(created["fire_at"], loc)
    logger.info(
        "_do_remind: scheduled chat_id=%s recurrence=%s fire_at=%s id=%s smart=%s",
        chat_id, created["recurrence"], when_str, created["id"], smart,
    )
    await update.message.reply_text(
        t(
            loc, "remind_set", when=when_str, text=created["text"],
            short_id=created["id"][:8], recurrence=created["recurrence"],
        ),
        parse_mode=ParseMode.HTML,
    )


async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _do_remind(update, context, smart=False)


async def cmd_remindai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Smart reminder: at fire time, runs a small tool loop (fetch_rss/get_weather/
    kb_search/search_conversations) over the prompt instead of sending static text."""
    await _do_remind(update, context, smart=True)


async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        return
    chat_id = update.effective_chat.id
    loc = _locale(chat_id)
    rows = await reminder_repo.list_for_chat(chat_id)
    if not rows:
        await update.message.reply_text(t(loc, "reminders_none"))
        return
    lines = [t(loc, "reminders_header")]
    for row in rows:
        label = row["smart_prompt"] or row["text"] or ""
        recurrence = "" if row["recurrence"] == "once" else f" [{row['recurrence']}]"
        lines.append(f"<code>{row['id'][:8]}</code> — {_fmt_when(row['fire_at'], loc)}{recurrence} — {label}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_unremind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        return
    chat_id = update.effective_chat.id
    loc = _locale(chat_id)
    if not context.args:
        await update.message.reply_text(t(loc, "unremind_usage"), parse_mode=ParseMode.HTML)
        return

    prefix = context.args[0]
    rows = await reminder_repo.list_for_chat(chat_id)
    match = next((r for r in rows if r["id"].startswith(prefix)), None)
    if not match:
        await update.message.reply_text(t(loc, "unremind_not_found"))
        return

    await reminder_service.delete(match["id"], chat_id=chat_id)
    logger.info("cmd_unremind: cancelled id=%s chat_id=%s", match["id"], chat_id)
    await update.message.reply_text(t(loc, "unremind_done"))


async def _cb_reminder_snooze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    reminder_id = query.data.removeprefix("remind_snooze:")
    ok = await reminder_service.snooze(reminder_id)
    if ok:
        loc = _locale(query.message.chat_id)
        await query.message.reply_text(t(loc, "remind_snoozed"))


async def _cb_reminder_repeat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    reminder_id = query.data.removeprefix("remind_repeat:")
    await reminder_service.repeat(reminder_id)


async def _cb_reminder_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    reminder_id = query.data.removeprefix("remind_delete:")
    chat_id = query.message.chat_id
    ok = await reminder_service.delete(reminder_id, chat_id=chat_id)
    if ok:
        loc = _locale(chat_id)
        await query.message.reply_text(t(loc, "remind_deleted"))
