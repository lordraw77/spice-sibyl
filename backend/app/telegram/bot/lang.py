"""/lang — per-chat UI language.

Extracted from the former single-file bot.py.
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.db import telegram_prefs_repository as prefs_repo
from app.telegram import i18n
from app.telegram.i18n import t

from .state import _is_allowed, _locale, _locales

logger = logging.getLogger(__name__)

# ── Language (/lang) ──────────────────────────────────────────────────────────

async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        return
    chat_id = update.effective_chat.id
    loc = _locale(chat_id)

    # /lang <code> sets directly; bare /lang shows an inline keyboard.
    if context.args:
        code = context.args[0].strip().lower()
        if code in i18n.SUPPORTED_LOCALES:
            await _set_locale(chat_id, code)
            await update.message.reply_text(
                t(code, "lang_set", label=i18n.SUPPORTED_LOCALES[code]), parse_mode=ParseMode.HTML
            )
            return

    buttons = [
        [InlineKeyboardButton(label, callback_data=f"lang:{code}")]
        for code, label in i18n.SUPPORTED_LOCALES.items()
    ]
    await update.message.reply_text(
        t(loc, "lang_choose"), reply_markup=InlineKeyboardMarkup(buttons)
    )


async def _cb_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    code = query.data.removeprefix("lang:")
    chat_id = query.message.chat_id
    if code not in i18n.SUPPORTED_LOCALES:
        return
    await _set_locale(chat_id, code)
    await query.edit_message_text(
        t(code, "lang_set", label=i18n.SUPPORTED_LOCALES[code]), parse_mode=ParseMode.HTML
    )


async def _set_locale(chat_id: int, code: str) -> None:
    _locales[chat_id] = code
    await prefs_repo.set_locale(chat_id, code)
