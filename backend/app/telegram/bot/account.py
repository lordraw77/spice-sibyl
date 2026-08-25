"""/link, /unlink and the profile lookup behind every linked-only command.

Extracted from the former single-file bot.py.
"""

import logging
import time

import aiosqlite
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import secrets

from app.core.config import settings

from .state import _is_allowed, _link_codes

logger = logging.getLogger(__name__)

# ── /link and /unlink ────────────────────────────────────────────────────────

async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        return
    code = secrets.token_hex(3).upper()
    _link_codes[code] = {"telegram_id": user.id, "username": user.username, "expires": time.time() + 300}
    # Also register in the endpoint module
    from app.api.v1.endpoints.telegram_link import register_link_code
    register_link_code(code, user.id, user.username)
    await update.message.reply_text(
        f"🔗 <b>Collega il tuo profilo web</b>\n\n"
        f"Inserisci questo codice nella sezione Telegram del tuo profilo web:\n\n"
        f"<code>{code}</code>\n\n"
        f"Il codice scade tra 5 minuti.",
        parse_mode=ParseMode.HTML,
    )


async def _linked_profile_id(telegram_id: int) -> str | None:
    """Return the web profile linked to this Telegram user, or None."""
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        from app.db import telegram_link_repository as tl_repo
        row = await tl_repo.get_by_telegram_id(db, telegram_id)
    return row["profile_id"] if row else None


async def cmd_unlink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        return
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        from app.db import telegram_link_repository as tl_repo
        row = await tl_repo.get_by_telegram_id(db, user.id)
        if not row:
            await update.message.reply_text("Non sei collegato a nessun profilo web.")
            return
        await tl_repo.unlink_by_profile(db, row["profile_id"])
    await update.message.reply_text("✅ Profilo web scollegato.")
