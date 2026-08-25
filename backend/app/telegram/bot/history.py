"""/history, /search, conversation resume and inline queries.

Extracted from the former single-file bot.py.
"""

import logging

import aiosqlite
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.core.config import settings
from app.db.search_repository import search_conversations
from app.db import telegram_prefs_repository as prefs_repo
from app.db import conversation_repository as conv_repo
from app.telegram.i18n import t
from app.dependencies.provider_factory import get_provider
from app.schemas.chat import ChatCompletionRequest, ChatMessage

from .account import _linked_profile_id
from .state import (
    _MAX_HISTORY,
    _active_convs,
    _default_model,
    _hydrated,
    _is_allowed,
    _locale,
    _models,
    _sessions,
    _split,
)

logger = logging.getLogger(__name__)

# ── /history command ─────────────────────────────────────────────────────────

def _channel_badge(channel: str) -> str:
    """Emoji marking a conversation's channel of origin in /history."""
    return "✈️" if channel == "telegram" else "🌐"


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List recent conversations.

    For linked chats (Phase 23.a): the profile's recent conversations across both
    channels, each with an inline button to resume it. For unlinked chats: the
    current in-memory session preview (legacy behaviour)."""
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("cmd_history: accesso negato user_id=%s", user.id)
        return

    chat_id = update.effective_chat.id
    loc = _locale(chat_id)
    profile_id = await _linked_profile_id(chat_id)

    if profile_id:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            conversations = await conv_repo.list_conversations(db, profile_id)
        if not conversations:
            await update.message.reply_text(t(loc, "history_empty"))
            return

        active_id = _active_convs.get(chat_id)
        buttons = []
        for conv in conversations[:12]:
            title = " ".join((conv.title or "—").split())[:50]
            marker = "✅ " if conv.id == active_id else ""
            label = f"{marker}{_channel_badge(conv.channel)} {title}"
            buttons.append([InlineKeyboardButton(label, callback_data=f"resume:{conv.id}")])
        await update.message.reply_text(
            t(loc, "history_title"),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=ParseMode.HTML,
        )
        return

    # Unlinked: preview the in-memory session (no persistence available).
    session = _sessions.get(chat_id, [])
    if not session:
        await update.message.reply_text(t(loc, "history_empty_unlinked"))
        return

    lines = [t(loc, "history_current_header") + "\n"]
    for msg in session[-20:]:
        role = "👤" if msg["role"] == "user" else "🤖"
        content = msg["content"]
        if isinstance(content, list):
            content = next((p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text"), "[media]")
        preview = content[:120].replace("<", "&lt;").replace(">", "&gt;")
        if len(content) > 120:
            preview += "…"
        lines.append(f"{role} {preview}")

    model = _models.get(chat_id, _default_model())
    lines.append(f"\n<i>{model} · {len(session)}</i>")

    text = "\n".join(lines)
    for chunk in _split(text):
        await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)


async def _cb_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resume a conversation picked from the /history keyboard (Phase 23.a)."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    loc = _locale(chat_id)
    if not _is_allowed(query.from_user.id):
        return

    conv_id = query.data.removeprefix("resume:")
    profile_id = await _linked_profile_id(chat_id)
    if not profile_id:
        return

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT profile_id FROM conversations WHERE id = ?", (conv_id,)
        ) as cur:
            owner = await cur.fetchone()
        conv = await conv_repo.get_conversation(db, conv_id) if owner else None
    if not conv or owner["profile_id"] != profile_id:
        await query.edit_message_text(t(loc, "history_resume_gone"))
        return

    _active_convs[chat_id] = conv_id
    _hydrated.add(chat_id)
    _sessions[chat_id] = [
        {"role": m.role, "content": m.content}
        for m in conv.messages
        if m.role in ("user", "assistant") and isinstance(m.content, str)
    ][-_MAX_HISTORY:]
    await prefs_repo.set_active_conversation(chat_id, conv_id)
    logger.info("cmd_history: ripresa conversazione chat_id=%s conv=%s", chat_id, conv_id)

    title = " ".join((conv.title or "—").split())[:60]
    await query.edit_message_text(
        t(loc, "history_resumed", title=title), parse_mode=ParseMode.HTML
    )


# ── /search command ──────────────────────────────────────────────────────────

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Full-text search across all saved conversations (SQLite FTS5)."""
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("cmd_search: accesso negato user_id=%s", user.id)
        return

    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await update.message.reply_text("Uso: /search <testo da cercare>")
        return

    chat_id = update.effective_chat.id
    logger.info("cmd_search: chat_id=%s query=%r", chat_id, query)

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        results = await search_conversations(db, query, limit=10)

    if not results:
        await update.message.reply_text(
            f"🔍 Nessun risultato per <code>{query}</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = [f"🔍 Risultati per <code>{query}</code>:\n"]
    for r in results:
        title = (r.title or "Senza titolo").replace("<", "&lt;").replace(">", "&gt;")
        snippet = (r.snippet or "").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"<b>{title}</b>")
        if r.model:
            lines.append(f"  <i>{r.model}</i>")
        if snippet:
            lines.append(f"  {snippet[:200]}")
        lines.append("")

    text = "\n".join(lines)
    for chunk in _split(text):
        await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)


# ── Inline query handler ────────────────────────────────────────────────────

async def handle_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answer @bot <query> inline queries with a short LLM response."""
    query = update.inline_query
    if not query:
        return
    user = query.from_user
    if not _is_allowed(user.id):
        return
    text = (query.query or "").strip()
    if len(text) < 3:
        await query.answer([], cache_time=5)
        return

    model = _models.get(user.id, _default_model())
    provider = get_provider(model)
    messages = [ChatMessage(role="user", content=text)]
    request = ChatCompletionRequest(model=model, messages=messages, max_tokens=300)

    try:
        result = await provider.complete(request)
        choices = result.get("choices") or []
        answer = (choices[0].get("message") or {}).get("content", "") if choices else ""
    except Exception as exc:
        logger.warning("handle_inline_query: errore model=%s: %s", model, exc)
        answer = f"Errore: {exc}"

    if not answer:
        answer = "Nessuna risposta dal modello."

    import hashlib
    result_id = hashlib.md5(f"{text}:{answer[:50]}".encode()).hexdigest()

    results = [
        InlineQueryResultArticle(
            id=result_id,
            title=answer[:100],
            description=f"via {model}",
            input_message_content=InputTextMessageContent(answer[:4096]),
        )
    ]
    await query.answer(results, cache_time=30)
    logger.info("handle_inline_query: user_id=%s query=%r model=%s", user.id, text[:60], model)
