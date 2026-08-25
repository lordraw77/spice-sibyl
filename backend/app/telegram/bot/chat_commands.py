"""/start, /new, /model, /agent, /chat, /models, /stats and the model keyboards.

Extracted from the former single-file bot.py.
"""

import logging
from collections import defaultdict

import aiosqlite
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.core.config import settings
from app.data.model_catalog import iter_configured_models
from app.db.stats_repository import get_usage_stats
from app.telegram.i18n import t

from .conversations import _reset_conversation
from .state import (
    _AGENT_MODEL,
    _callback_models,
    _chat_models,
    _default_model,
    _is_agent_model,
    _is_allowed,
    _locale,
    _models,
    _split,
)

logger = logging.getLogger(__name__)

# ── Command handlers ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("cmd_start: accesso negato user_id=%s username=%s", user.id, user.username)
        return
    chat_id = update.effective_chat.id
    logger.info("cmd_start: user_id=%s username=%s chat_id=%s locale=%s", user.id, user.username, chat_id, _locale(chat_id))
    model = _models.get(chat_id, _default_model())
    await update.message.reply_text(
        t(_locale(chat_id), "start", model=model),
        parse_mode=ParseMode.HTML,
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("cmd_new: accesso negato user_id=%s", user.id)
        return
    logger.info("cmd_new: reset sessione chat_id=%s user_id=%s", update.effective_chat.id, user.id)
    await _reset_conversation(update.effective_chat.id)
    await update.message.reply_text(t(_locale(update.effective_chat.id), "new_cleared"))


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("cmd_model: accesso negato user_id=%s", user.id)
        return
    chat_id = update.effective_chat.id
    args = context.args

    if args:
        model_id = args[0].strip()
        logger.info("cmd_model: cambio modello chat_id=%s old=%s new=%s", chat_id, _models.get(chat_id, _default_model()), model_id)
        _models[chat_id] = model_id
        await _reset_conversation(chat_id)
        await update.message.reply_text(
            f"✅ Modello impostato: <code>{model_id}</code>\nConversazione azzerata.",
            parse_mode=ParseMode.HTML,
        )
        return

    current = _models.get(chat_id, _default_model())
    all_models = iter_configured_models()
    providers: dict[str, list[str]] = defaultdict(list)
    for m in all_models:
        providers[m.get("provider", "other")].append(m["id"])

    buttons = [
        [InlineKeyboardButton(
            f"{'✅ ' if any(mid == current for mid in ids) else ''}{prov} ({len(ids)})",
            callback_data=f"mp:{prov}",
        )]
        for prov, ids in sorted(providers.items())
    ]
    await update.message.reply_text(
        f"Modello corrente: <code>{current}</code>\n\nScegli un provider:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _cb_model_provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: user tapped a provider button — show its models."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    provider = query.data.removeprefix("mp:")

    current = _models.get(chat_id, _default_model())
    all_models = iter_configured_models()
    models = [m["id"] for m in all_models if (m.get("provider") or "other") == provider]

    if not models:
        await query.edit_message_text(f"Nessun modello per <b>{provider}</b>.", parse_mode=ParseMode.HTML)
        return

    _callback_models[chat_id] = models
    buttons = []
    for idx, mid in enumerate(models):
        label = ("✅ " if mid == current else "") + mid.split("/", 1)[-1]
        buttons.append([InlineKeyboardButton(label, callback_data=f"ms:{idx}")])
    buttons.append([InlineKeyboardButton("« Indietro", callback_data="mp:__back__")])

    await query.edit_message_text(
        f"<b>{provider}</b> — scegli un modello:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _cb_model_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: user tapped a model button — apply selection."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    idx = int(query.data.removeprefix("ms:"))
    models = _callback_models.get(chat_id, [])

    if idx < 0 or idx >= len(models):
        await query.edit_message_text("⚠ Selezione non valida.")
        return

    model_id = models[idx]
    old = _models.get(chat_id, _default_model())
    logger.info("cmd_model: cambio modello (inline) chat_id=%s old=%s new=%s", chat_id, old, model_id)
    _models[chat_id] = model_id
    await _reset_conversation(chat_id)
    await query.edit_message_text(
        f"✅ Modello impostato: <code>{model_id}</code>\nConversazione azzerata.",
        parse_mode=ParseMode.HTML,
    )


async def _cb_model_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: user tapped 'back' — re-show provider list."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    current = _models.get(chat_id, _default_model())
    all_models = iter_configured_models()
    providers: dict[str, list[str]] = defaultdict(list)
    for m in all_models:
        providers[m.get("provider", "other")].append(m["id"])
    buttons = [
        [InlineKeyboardButton(
            f"{'✅ ' if any(mid == current for mid in ids) else ''}{prov} ({len(ids)})",
            callback_data=f"mp:{prov}",
        )]
        for prov, ids in sorted(providers.items())
    ]
    await query.edit_message_text(
        f"Modello corrente: <code>{current}</code>\n\nScegli un provider:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cmd_agent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Switch this chat to the Multi-MCP orchestrator (agent mode)."""
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("cmd_agent: accesso negato user_id=%s", user.id)
        return
    chat_id = update.effective_chat.id
    logger.info("cmd_agent: attivazione agent mode chat_id=%s user_id=%s", chat_id, user.id)
    current = _models.get(chat_id, _default_model())
    if not _is_agent_model(current):
        _chat_models[chat_id] = current  # remember to restore on /chat
    _models[chat_id] = _AGENT_MODEL
    await _reset_conversation(chat_id)
    await update.message.reply_text(
        f"🤖 Modalità <b>agente</b>: <code>{_AGENT_MODEL}</code>\n"
        f"Delego a Proxmox · Synology · Linux · HAOS · WatchYourLAN.\n"
        f"Conversazione azzerata. Torna alla chat con /chat.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Switch this chat back to a normal chat model.

    /chat            → restore the last chat model (or the default)
    /chat <model_id> → switch to a specific chat model
    """
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("cmd_chat: accesso negato user_id=%s", user.id)
        return
    chat_id = update.effective_chat.id

    if context.args:
        target = context.args[0].strip()
    else:
        target = _chat_models.get(chat_id) or _default_model()
        if _is_agent_model(target):
            target = _default_model()

    logger.info("cmd_chat: ritorno a chat mode chat_id=%s model=%s user_id=%s", chat_id, target, user.id)
    _models[chat_id] = target
    if not _is_agent_model(target):
        _chat_models[chat_id] = target
    await _reset_conversation(chat_id)
    await update.message.reply_text(
        f"💬 Modalità <b>chat</b>: <code>{target}</code>\n"
        f"Conversazione azzerata. Passa all'agente con /agent.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_models(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("cmd_models: accesso negato user_id=%s", user.id)
        return

    query = ' '.join(context.args).strip().lower() if context.args else ''
    logger.info("cmd_models: lista modelli chat_id=%s query=%r", update.effective_chat.id, query or "(all)")
    all_models = iter_configured_models()

    if query:
        filtered = [
            m for m in all_models
            if query in m['id'].lower()
            or query in (m.get('provider') or '').lower()
            or any(query in cap.lower() for cap in m.get('capabilities') or [])
            or query in (m.get('label') or '').lower()
        ]
    else:
        filtered = all_models

    if not filtered:
        await update.message.reply_text(
            f"Nessun modello trovato per <code>{query}</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    models_by_provider: dict[str, list[str]] = defaultdict(list)
    for m in filtered:
        models_by_provider[m.get('provider', 'other')].append(m['id'])

    header = f"🔍 Filtro: <i>{query}</i>\n\n" if query else ''
    lines = [header] if header else []
    for provider, ids in sorted(models_by_provider.items()):
        lines.append(f"<b>{provider}</b>")
        for mid in ids[:10]:
            lines.append(f"  <code>{mid}</code>")
        if len(ids) > 10:
            lines.append(f"  … +{len(ids) - 10} altri")

    text = '\n'.join(lines)
    for chunk in _split(text):
        await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("cmd_stats: accesso negato user_id=%s", user.id)
        return

    logger.info("cmd_stats: richiesta statistiche chat_id=%s user_id=%s", update.effective_chat.id, user.id)
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        usage = await get_usage_stats(db)

    g = usage.global_stats

    def fmt_cost(v: float) -> str:
        if v == 0:
            return '—'
        if v < 0.0001:
            return '< $0.0001'
        return f'${v:.4f}'

    lines = [
        "📊 <b>Statistiche di utilizzo</b>\n",
        f"💬 Conversazioni: <b>{g.total_conversations:,}</b>",
        f"📨 Messaggi:       <b>{g.total_messages:,}</b>",
        f"🔢 Token totali:   <b>{g.total_tokens:,}</b>",
        f"   ├ prompt:       {g.total_prompt_tokens:,}",
        f"   └ completion:   {g.total_completion_tokens:,}",
        f"💰 Costo stimato:  <b>{fmt_cost(g.total_cost)}</b>",
    ]

    if usage.by_provider:
        lines.append("\n<b>Per provider</b>")
        for p in usage.by_provider[:8]:
            name = p.provider or 'unknown'
            cost = fmt_cost(p.estimated_cost)
            lines.append(
                f"  <code>{name}</code> — {p.total_tokens:,} tkn · {cost}"
            )

    text = '\n'.join(lines)
    for chunk in _split(text):
        await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
