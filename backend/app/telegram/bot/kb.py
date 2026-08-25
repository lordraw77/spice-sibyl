"""/kb — knowledge-base search from the chat.

Extracted from the former single-file bot.py.
"""

import logging

import aiosqlite
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.core.config import settings
from app.telegram.i18n import t

from .account import _linked_profile_id
from .state import _is_allowed, _locale

logger = logging.getLogger(__name__)

async def cmd_kb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Phase 21: /kb list|del <id> — manage the linked profile's knowledge base.

    Document ingestion (send a file with a /kb caption) is handled in
    handle_document; this command only lists and removes documents.
    """
    user = update.effective_user
    if not _is_allowed(user.id):
        return
    chat_id = update.effective_chat.id
    loc = _locale(chat_id)
    args = [a.strip() for a in (context.args or []) if a.strip()]
    sub = args[0].lower() if args else ""

    if sub not in ("list", "del"):
        await update.message.reply_text(t(loc, "kb_usage"), parse_mode=ParseMode.HTML)
        return

    profile_id = await _linked_profile_id(user.id)
    if not profile_id:
        await update.message.reply_text(t(loc, "kb_not_linked"))
        return

    from app.db import kb_repository
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        if sub == "list":
            docs = await kb_repository.list_documents(db, profile_id)
            if not docs:
                await update.message.reply_text(t(loc, "kb_empty"))
                return
            icons = {"ready": "✅", "pending": "⏳", "error": "⚠️"}
            lines = [
                f"{icons.get(d.status, '•')} <code>{d.id[:8]}</code> "
                f"{'🔗 ' if d.source_type == 'url' else ''}{d.filename} "
                f"({d.chunk_count} chunk)"
                for d in docs[:30]
            ]
            await update.message.reply_text(
                t(loc, "kb_header") + "\n".join(lines), parse_mode=ParseMode.HTML
            )
            return

        # del <id>
        if len(args) < 2:
            await update.message.reply_text(t(loc, "kb_del_usage"), parse_mode=ParseMode.HTML)
            return
        prefix = args[1]
        docs = await kb_repository.list_documents(db, profile_id)
        match = next((d for d in docs if d.id.startswith(prefix)), None)
        if not match:
            await update.message.reply_text(t(loc, "kb_not_found"))
            return
        await kb_repository.delete_document(db, match.id)
    await update.message.reply_text(t(loc, "kb_deleted"))
