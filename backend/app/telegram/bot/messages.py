"""The plain-text message handler.

Extracted from the former single-file bot.py.
"""

import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from .conversations import _ensure_hydrated
from .state import _MAX_HISTORY, _default_model, _is_allowed, _models, _sessions, counters
from .streaming import _stream_reply

logger = logging.getLogger(__name__)

# ── Message handler ──────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    if not update.message or not update.message.text:
        return
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("handle_message: accesso negato user_id=%s username=%s", user.id, user.username)
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return

    counters.received += 1

    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    model = _models.get(chat_id, _default_model())
    logger.info("handle_message: chat_id=%s user_id=%s model=%s text_len=%d", chat_id, user.id, model, len(text))

    await _ensure_hydrated(chat_id)
    session = _sessions[chat_id]
    session.append({"role": "user", "content": text})

    if len(session) > _MAX_HISTORY:
        _sessions[chat_id] = session[-_MAX_HISTORY:]
        session = _sessions[chat_id]

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    sent = await update.message.reply_text("⏳")

    await _stream_reply(chat_id, session, model, sent, update, persist_user=text)
