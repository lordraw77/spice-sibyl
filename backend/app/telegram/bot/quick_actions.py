"""The inline quick-action keyboard callbacks.

Extracted from the former single-file bot.py.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from .state import _MAX_HISTORY, _default_model, _is_allowed, _models, _sessions
from .streaming import _stream_reply

logger = logging.getLogger(__name__)

async def _cb_quick_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle quick-action button taps after an assistant reply."""

    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user = query.from_user
    if not _is_allowed(user.id):
        return

    action = query.data.removeprefix("qa:")
    session = _sessions[chat_id]
    model = _models.get(chat_id, _default_model())

    if action == "regenerate":
        if len(session) >= 2 and session[-1]["role"] == "assistant":
            session.pop()
        elif not session or session[-1]["role"] != "user":
            await query.edit_message_reply_markup(reply_markup=None)
            return
    elif action == "translate":
        if not session or session[-1]["role"] != "assistant":
            await query.edit_message_reply_markup(reply_markup=None)
            return
        session.append({"role": "user", "content": "Traduci la tua ultima risposta in inglese. Se è già in inglese, traducila in italiano."})
    elif action == "summarize":
        if not session or session[-1]["role"] != "assistant":
            await query.edit_message_reply_markup(reply_markup=None)
            return
        session.append({"role": "user", "content": "Riassumi brevemente la tua ultima risposta in pochi punti chiave."})
    elif action == "continue":
        if not session or session[-1]["role"] != "assistant":
            await query.edit_message_reply_markup(reply_markup=None)
            return
        session.append({"role": "user", "content": "Continua."})
    else:
        return

    await query.edit_message_reply_markup(reply_markup=None)

    if len(session) > _MAX_HISTORY:
        _sessions[chat_id] = session[-_MAX_HISTORY:]
        session = _sessions[chat_id]

    sent = await query.message.reply_text("⏳")
    await _stream_reply(chat_id, session, model, sent, update=None, orig_message=query.message)
