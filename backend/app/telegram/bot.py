"""
Telegram bot for SpiceSibyl.

Each Telegram chat gets its own in-memory conversation history and a chosen
model.  The bot streams the provider response and edits the reply message as
tokens arrive (throttled to ~1 edit/s to stay within Telegram rate limits).

Commands:
  /start            — welcome message
  /new              — clear conversation history for this chat
  /model            — inline keyboard to pick a model
  /model <id>       — switch to a different model
  /models           — list all available models grouped by provider
  /models <query>   — filter models by provider, capability or name
  /history          — list recent conversations in this chat
  /search <query>   — full-text search past messages
  /stats            — show global usage statistics
  /remind <when> <t>— schedule a reminder (HH:MM or +30m/2h/1d), persisted + JobQueue
  /reminders        — list pending reminders; /unremind <id> cancels
  /lang             — switch the bot UI language per chat (it/en)
  Any text          — sent to the active model, reply streamed back
  Voice/audio       — transcribed via Whisper, then processed as text

Localized user-facing strings live in app/telegram/i18n.py.
"""

import asyncio
import base64
import io
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite
import httpx
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from app.core.config import settings
from app.data.model_catalog import iter_configured_models
from app.db.search_repository import search_conversations
from app.db.stats_repository import get_usage_stats
from app.db import telegram_reminder_repository as reminder_repo
from app.db import telegram_prefs_repository as prefs_repo
from app.telegram import i18n
from app.telegram.i18n import t
from app.dependencies.provider_factory import get_provider
from app.schemas.chat import ChatCompletionRequest, ChatMessage
from app.services.image_service import generate_image, ImageGenerationError, get_available_provider

logger = logging.getLogger(__name__)

# ── Session state ────────────────────────────────────────────────────────────

# chat_id → list of message dicts (role/content only, no telemetry)
_sessions: dict[int, list[dict]] = defaultdict(list)

# chat_id → model id string (the active model)
_models: dict[int, str] = {}

# chat_id → last non-agent model, remembered so /chat can restore it
_chat_models: dict[int, str] = {}

_MAX_HISTORY = 40  # keep last 40 messages (~20 exchanges) per chat

# The multi-agent orchestrator model (routed to the Multi-MCP sidecar)
_AGENT_MODEL = "agent/multi-mcp"

# chat_id → list of model IDs for the current inline-keyboard selection
_callback_models: dict[int, list[str]] = {}

# message_id → chat_id mapping for quick-action buttons
_action_messages: dict[int, int] = {}

# chat_id → UI locale (warm-started from telegram_prefs at boot, updated on /lang)
_locales: dict[int, str] = {}


def _locale(chat_id: int) -> str:
    """Resolve the UI locale for a chat (synchronous, cache-backed)."""
    return _locales.get(chat_id, i18n.DEFAULT_LOCALE)

# Temporary link codes: code → {telegram_id, username, expires}
import secrets
_link_codes: dict[str, dict] = {}


def _is_agent_model(model: str) -> bool:
    return model.startswith("agent/")

# ── In-memory counters ───────────────────────────────────────────────────────

_tg_messages_received: int = 0
_tg_messages_sent: int = 0
_tg_errors: int = 0


def get_telegram_stats() -> dict:
    return {
        "active_chats": len([s for s in _sessions.values() if s]),
        "messages_received": _tg_messages_received,
        "messages_sent": _tg_messages_sent,
        "errors": _tg_errors,
    }

# ── Access control ───────────────────────────────────────────────────────────

def _allowed_users() -> set[int] | None:
    raw = settings.telegram_allowed_users
    if not raw:
        return None
    return {int(uid.strip()) for uid in raw.split(',') if uid.strip().isdigit()}


def _is_allowed(user_id: int) -> bool:
    allowed = _allowed_users()
    return allowed is None or user_id in allowed


def _default_model() -> str:
    return settings.telegram_default_model or settings.default_model


# ── Helpers ──────────────────────────────────────────────────────────────────

def _split(text: str, limit: int = 4000) -> list[str]:
    """Split text into Telegram-safe chunks (≤ limit chars)."""
    if len(text) <= limit:
        return [text]
    parts, buf = [], []
    for line in text.splitlines(keepends=True):
        if sum(len(l) for l in buf) + len(line) > limit:
            parts.append(''.join(buf))
            buf = []
        buf.append(line)
    if buf:
        parts.append(''.join(buf))
    return parts or [text[:limit]]


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
    _sessions[update.effective_chat.id].clear()
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
        _sessions[chat_id].clear()
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
    _sessions[chat_id].clear()
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
    _sessions[chat_id].clear()
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
    _sessions[chat_id].clear()
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


# ── /imagine command ─────────────────────────────────────────────────────────

async def cmd_imagine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate an image from a text prompt: /imagine <prompt>"""
    global _tg_messages_sent, _tg_errors

    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("cmd_imagine: accesso negato user_id=%s", user.id)
        return

    prompt = ' '.join(context.args).strip() if context.args else ''
    if not prompt:
        await update.message.reply_text("Uso: /imagine <descrizione dell'immagine>")
        return

    chat_id = update.effective_chat.id
    logger.info("cmd_imagine: chat_id=%s prompt=%r", chat_id, prompt[:80])

    if not get_available_provider():
        await update.message.reply_text("⚠ Nessun provider di generazione immagini configurato.")
        return

    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
    sent = await update.message.reply_text("🎨 Generazione in corso…")

    try:
        result = await generate_image(prompt=prompt)
        image_bytes = base64.b64decode(result["b64_json"])
        await update.message.reply_photo(
            photo=io.BytesIO(image_bytes),
            caption=f"🎨 {prompt[:200]}\n\n<i>{result['provider']} · {result['model']}</i>",
            parse_mode=ParseMode.HTML,
        )
        await sent.delete()
        _tg_messages_sent += 1
        logger.info("cmd_imagine: immagine generata chat_id=%s provider=%s", chat_id, result["provider"])
    except ImageGenerationError as exc:
        _tg_errors += 1
        logger.warning("cmd_imagine: errore generazione chat_id=%s: %s", chat_id, exc)
        try:
            await sent.edit_text(f"⚠ Errore: {exc}")
        except Exception:
            pass
    except Exception as exc:
        _tg_errors += 1
        logger.exception("cmd_imagine: errore imprevisto chat_id=%s", chat_id)
        try:
            await sent.edit_text(f"⚠ Errore: {exc}")
        except Exception:
            pass


# ── Photo handler (image-to-text / vision) ──────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photos sent to the bot — use a vision-capable model to describe them."""
    global _tg_messages_received, _tg_messages_sent, _tg_errors

    if not update.message or not update.message.photo:
        return
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("handle_photo: accesso negato user_id=%s", user.id)
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return

    _tg_messages_received += 1
    chat_id = update.effective_chat.id
    caption = (update.message.caption or "").strip() or "Descrivi questa immagine in dettaglio."
    model = _models.get(chat_id, _default_model())
    logger.info("handle_photo: chat_id=%s model=%s caption=%r", chat_id, model, caption[:60])

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    sent = await update.message.reply_text("⏳")

    # Download the highest-resolution photo
    photo = update.message.photo[-1]
    try:
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
        b64_data = base64.b64encode(photo_bytes).decode()
        data_url = f"data:image/jpeg;base64,{b64_data}"
    except Exception as exc:
        _tg_errors += 1
        logger.error("handle_photo: download foto fallito chat_id=%s: %s", chat_id, exc)
        try:
            await sent.edit_text("⚠ Impossibile scaricare la foto.")
        except Exception:
            pass
        return

    # Build multimodal message content
    vision_content = [
        {"type": "text", "text": caption},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]

    session = _sessions[chat_id]
    session.append({"role": "user", "content": vision_content})

    if len(session) > _MAX_HISTORY:
        _sessions[chat_id] = session[-_MAX_HISTORY:]
        session = _sessions[chat_id]

    provider = get_provider(model)
    messages = [ChatMessage(role=m["role"], content=m["content"]) for m in session]
    request = ChatCompletionRequest(model=model, messages=messages, max_tokens=2048)

    full_content = ""
    last_edit = time.monotonic()

    try:
        async for chunk in provider.stream(request):
            if chunk.get("object") == "chat.completion.meta":
                break
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = (choices[0].get("delta") or {}).get("content") or ""
            if not delta:
                continue
            full_content += delta
            now = time.monotonic()
            if now - last_edit >= 1.0:
                try:
                    await sent.edit_text(full_content + " ▌")
                    last_edit = now
                except Exception:
                    pass

        chunks = _split(full_content or "⚠ Nessuna risposta.")
        await sent.edit_text(chunks[0])
        for extra in chunks[1:]:
            await update.message.reply_text(extra)

        if full_content:
            session.append({"role": "assistant", "content": full_content})
            _tg_messages_sent += 1
            logger.info("handle_photo: risposta completata chat_id=%s response_len=%d", chat_id, len(full_content))

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _tg_errors += 1
        logger.exception("handle_photo: errore chat_id=%s model=%s", chat_id, model)
        try:
            await sent.edit_text(f"⚠ Errore: {exc}")
        except Exception:
            pass
        if session and session[-1]["role"] == "user":
            session.pop()


# ── Voice / audio handler ───────────────────────────────────────────────────

async def _transcribe_audio(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    """Transcribe audio via Groq Whisper API. Falls back with a clear error."""
    api_key = settings.groq_api_key
    if not api_key:
        raise RuntimeError("GROQ_API_KEY non configurata — impossibile trascrivere l'audio")

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (filename, audio_bytes, "audio/ogg")},
            data={"model": "whisper-large-v3", "language": "it"},
        )
        resp.raise_for_status()
        return resp.json().get("text", "").strip()


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice/audio messages: transcribe via Whisper, then process as text."""
    global _tg_messages_received, _tg_messages_sent, _tg_errors

    msg = update.message
    if not msg:
        return
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("handle_voice: accesso negato user_id=%s", user.id)
        await msg.reply_text("⛔ Accesso non autorizzato.")
        return

    _tg_messages_received += 1
    chat_id = update.effective_chat.id
    logger.info("handle_voice: chat_id=%s user_id=%s", chat_id, user.id)

    voice = msg.voice or msg.audio
    if not voice:
        return

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    sent = await msg.reply_text("🎙️ Trascrizione in corso…")

    try:
        file = await context.bot.get_file(voice.file_id)
        audio_bytes = await file.download_as_bytearray()
    except Exception as exc:
        _tg_errors += 1
        logger.error("handle_voice: download audio fallito chat_id=%s: %s", chat_id, exc)
        try:
            await sent.edit_text("⚠ Impossibile scaricare l'audio.")
        except Exception:
            pass
        return

    try:
        transcript = await _transcribe_audio(bytes(audio_bytes))
    except Exception as exc:
        _tg_errors += 1
        logger.error("handle_voice: trascrizione fallita chat_id=%s: %s", chat_id, exc)
        try:
            await sent.edit_text(f"⚠ Trascrizione fallita: {exc}")
        except Exception:
            pass
        return

    if not transcript:
        try:
            await sent.edit_text("⚠ Nessun testo riconosciuto nell'audio.")
        except Exception:
            pass
        return

    logger.info("handle_voice: trascritto chat_id=%s len=%d", chat_id, len(transcript))
    try:
        await sent.edit_text(f"🎙️ <i>{transcript}</i>", parse_mode=ParseMode.HTML)
    except Exception:
        pass

    model = _models.get(chat_id, _default_model())
    session = _sessions[chat_id]
    session.append({"role": "user", "content": transcript})
    if len(session) > _MAX_HISTORY:
        _sessions[chat_id] = session[-_MAX_HISTORY:]
        session = _sessions[chat_id]

    reply_sent = await msg.reply_text("⏳")
    await _stream_reply(chat_id, session, model, reply_sent, update)


# ── Quick-action buttons ────────────────────────────────────────────────────

_QUICK_ACTIONS = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🔄 Rigenera", callback_data="qa:regenerate"),
        InlineKeyboardButton("🌐 Traduci", callback_data="qa:translate"),
    ],
    [
        InlineKeyboardButton("📝 Riassumi", callback_data="qa:summarize"),
        InlineKeyboardButton("➡️ Continua", callback_data="qa:continue"),
    ],
])


async def _cb_quick_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle quick-action button taps after an assistant reply."""
    global _tg_messages_sent, _tg_errors

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


# ── Shared streaming helper ─────────────────────────────────────────────────

async def _stream_reply(
    chat_id: int,
    session: list[dict],
    model: str,
    sent,  # the placeholder Message we edit
    update: Update | None,
    orig_message=None,
) -> None:
    """Stream a provider response, edit *sent* as tokens arrive, attach quick-action buttons."""
    global _tg_messages_sent, _tg_errors

    # Bind a request id so Telegram-originated provider/sidecar logs correlate.
    from app.core.logging_context import set_request_id
    set_request_id()

    provider = get_provider(model)
    messages = [ChatMessage(role=m["role"], content=m["content"]) for m in session]
    request = ChatCompletionRequest(model=model, messages=messages, max_tokens=2048)

    full_content = ""
    progress: list[str] = []
    last_edit = time.monotonic()

    try:
        async for chunk in provider.stream(request):
            if chunk.get("object") == "chat.completion.meta":
                break

            sse_event = chunk.get("_sse_event")
            if sse_event == "tool_call":
                progress.append(f"🔧 {chunk.get('name', 'agent')} …")
                now = time.monotonic()
                if now - last_edit >= 1.0:
                    try:
                        await sent.edit_text("\n".join(progress))
                        last_edit = now
                    except Exception:
                        pass
                continue
            if sse_event == "tool_result":
                if progress:
                    progress[-1] = progress[-1].replace("🔧", "✅").removesuffix(" …")
                continue

            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = (choices[0].get("delta") or {}).get("content") or ""
            if not delta:
                continue

            full_content += delta
            now = time.monotonic()
            if now - last_edit >= 1.0:
                try:
                    await sent.edit_text(full_content + " ▌")
                    last_edit = now
                except Exception:
                    pass

        chunks = _split(full_content or "⚠ Nessuna risposta.")
        if len(chunks) == 1:
            await sent.edit_text(chunks[0], reply_markup=_QUICK_ACTIONS)
            _action_messages[sent.message_id] = chat_id
        else:
            await sent.edit_text(chunks[0])
            for extra in chunks[1:-1]:
                reply_target = update.message if update and update.message else orig_message
                if reply_target:
                    await reply_target.reply_text(extra)
            last_msg = None
            reply_target = update.message if update and update.message else orig_message
            if reply_target:
                last_msg = await reply_target.reply_text(chunks[-1], reply_markup=_QUICK_ACTIONS)
            if last_msg:
                _action_messages[last_msg.message_id] = chat_id

        if full_content:
            session.append({"role": "assistant", "content": full_content})
            _tg_messages_sent += 1
            logger.info("stream_reply: completata chat_id=%s model=%s len=%d", chat_id, model, len(full_content))
        else:
            logger.warning("stream_reply: risposta vuota chat_id=%s model=%s", chat_id, model)

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _tg_errors += 1
        logger.exception("stream_reply: errore chat_id=%s model=%s", chat_id, model)
        try:
            await sent.edit_text(f"⚠ Errore: {exc}")
        except Exception:
            pass
        if session and session[-1]["role"] == "user":
            session.pop()


# ── Message handler ──────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _tg_messages_received, _tg_messages_sent, _tg_errors

    if not update.message or not update.message.text:
        return
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("handle_message: accesso negato user_id=%s username=%s", user.id, user.username)
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return

    _tg_messages_received += 1

    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    model = _models.get(chat_id, _default_model())
    logger.info("handle_message: chat_id=%s user_id=%s model=%s text_len=%d", chat_id, user.id, model, len(text))

    session = _sessions[chat_id]
    session.append({"role": "user", "content": text})

    if len(session) > _MAX_HISTORY:
        _sessions[chat_id] = session[-_MAX_HISTORY:]
        session = _sessions[chat_id]

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    sent = await update.message.reply_text("⏳")

    await _stream_reply(chat_id, session, model, sent, update)


# ── /history command ─────────────────────────────────────────────────────────

async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent conversation exchanges in this chat's in-memory session."""
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("cmd_history: accesso negato user_id=%s", user.id)
        return

    chat_id = update.effective_chat.id
    session = _sessions.get(chat_id, [])

    if not session:
        await update.message.reply_text("📭 Nessun messaggio nella conversazione corrente.\nUsa /search per cercare nelle conversazioni salvate.")
        return

    lines = ["📜 <b>Conversazione corrente</b>\n"]
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
    lines.append(f"\n<i>Modello: {model} · {len(session)} messaggi</i>")

    text = "\n".join(lines)
    for chunk in _split(text):
        await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)


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


# ── Document handler (PDF, TXT, DOCX) ──────────────────────────────────────

_SUPPORTED_MIME = {
    "application/pdf",
    "text/plain",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

_MAX_DOC_CHARS = 8000


def _extract_text_from_pdf(data: bytes) -> str:
    from PyPDF2 import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        t = page.extract_text()
        if t:
            pages.append(t)
    return "\n\n".join(pages)


def _extract_text_from_docx(data: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(data))
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _extract_text_from_txt(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle PDF, TXT, DOCX documents: extract text and send as context to the model."""
    global _tg_messages_received, _tg_messages_sent, _tg_errors

    msg = update.message
    if not msg or not msg.document:
        return
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("handle_document: accesso negato user_id=%s", user.id)
        await msg.reply_text("⛔ Accesso non autorizzato.")
        return

    doc = msg.document
    mime = doc.mime_type or ""
    fname = doc.file_name or "file"

    if mime not in _SUPPORTED_MIME:
        await msg.reply_text(
            f"⚠ Formato non supportato: <code>{mime}</code>\n"
            f"Formati accettati: PDF, TXT, DOCX",
            parse_mode=ParseMode.HTML,
        )
        return

    _tg_messages_received += 1
    chat_id = update.effective_chat.id
    caption = (msg.caption or "").strip() or "Analizza il contenuto di questo documento."
    model = _models.get(chat_id, _default_model())
    logger.info("handle_document: chat_id=%s file=%s mime=%s model=%s", chat_id, fname, mime, model)

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    sent = await msg.reply_text("📄 Estrazione testo…")

    try:
        file = await context.bot.get_file(doc.file_id)
        file_bytes = await file.download_as_bytearray()
    except Exception as exc:
        _tg_errors += 1
        logger.error("handle_document: download fallito chat_id=%s: %s", chat_id, exc)
        try:
            await sent.edit_text("⚠ Impossibile scaricare il file.")
        except Exception:
            pass
        return

    try:
        if mime == "application/pdf":
            extracted = _extract_text_from_pdf(bytes(file_bytes))
        elif mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            extracted = _extract_text_from_docx(bytes(file_bytes))
        else:
            extracted = _extract_text_from_txt(bytes(file_bytes))
    except Exception as exc:
        _tg_errors += 1
        logger.error("handle_document: estrazione fallita chat_id=%s: %s", chat_id, exc)
        try:
            await sent.edit_text(f"⚠ Errore nell'estrazione del testo: {exc}")
        except Exception:
            pass
        return

    if not extracted.strip():
        try:
            await sent.edit_text("⚠ Nessun testo estraibile dal documento.")
        except Exception:
            pass
        return

    truncated = ""
    if len(extracted) > _MAX_DOC_CHARS:
        extracted = extracted[:_MAX_DOC_CHARS]
        truncated = f"\n\n[Documento troncato a {_MAX_DOC_CHARS} caratteri]"

    doc_context = f"📄 **{fname}**\n\n{extracted}{truncated}"

    try:
        await sent.edit_text(
            f"📄 Testo estratto ({len(extracted)} caratteri). Elaborazione in corso…"
        )
    except Exception:
        pass

    session = _sessions[chat_id]
    session.append({"role": "user", "content": f"{caption}\n\n{doc_context}"})
    if len(session) > _MAX_HISTORY:
        _sessions[chat_id] = session[-_MAX_HISTORY:]
        session = _sessions[chat_id]

    reply_sent = await msg.reply_text("⏳")
    await _stream_reply(chat_id, session, model, reply_sent, update)


# ── Reminders (/remind) ───────────────────────────────────────────────────────

_REL_RE = re.compile(r"^\+?(\d+)\s*([mhd])$", re.IGNORECASE)
_ABS_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_REL_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}


def _tz() -> ZoneInfo:
    """Configured display/parsing timezone (independent of the container TZ)."""
    try:
        return ZoneInfo(settings.timezone)
    except Exception:
        logger.warning("Timezone %r non valida, uso UTC", settings.timezone)
        return ZoneInfo("UTC")


def _fmt_when(fire_at: int) -> str:
    return datetime.fromtimestamp(fire_at, _tz()).strftime("%d/%m %H:%M")


def _parse_when(token: str) -> int | None:
    """Parse a time token into an absolute unix timestamp.

    Times are interpreted in the configured timezone (settings.timezone), so
    "/remind 15:50" means 15:50 local regardless of the container's system TZ.
    Accepts relative ("+30m", "2h", "1d") and absolute ("15:50") forms.  For an
    absolute time already past today, schedules the next day.  Returns None if
    the token isn't a recognized time.
    """
    now = datetime.now(_tz())
    rel = _REL_RE.match(token)
    if rel:
        amount = int(rel.group(1))
        unit = rel.group(2).lower()
        return int((now + timedelta(seconds=amount * _REL_UNIT_SECONDS[unit])).timestamp())

    absolute = _ABS_RE.match(token)
    if absolute:
        hour, minute = int(absolute.group(1)), int(absolute.group(2))
        if hour > 23 or minute > 59:
            return None
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return int(target.timestamp())

    return None


async def _fire_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback: deliver a reminder and mark it fired."""
    data = context.job.data or {}
    reminder_id = data.get("id")
    chat_id = data.get("chat_id")
    text = data.get("text", "")
    logger.info("_fire_reminder: delivering id=%s chat_id=%s", reminder_id, chat_id)
    try:
        await context.bot.send_message(chat_id=chat_id, text=t(_locale(chat_id), "remind_fired", text=text))
    except Exception:
        logger.exception("_fire_reminder: send failed id=%s chat_id=%s", reminder_id, chat_id)
    finally:
        if reminder_id:
            await reminder_repo.mark_fired(reminder_id)


def _schedule_reminder(job_queue, reminder_id: str, chat_id: int, text: str, fire_at: int) -> None:
    """Register a JobQueue job that fires at fire_at (or immediately if past)."""
    delay = max(fire_at - int(time.time()), 0)
    job_queue.run_once(
        _fire_reminder,
        when=delay,
        data={"id": reminder_id, "chat_id": chat_id, "text": text},
        name=f"reminder:{reminder_id}",
    )


async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        logger.warning("cmd_remind: accesso negato user_id=%s", user.id)
        return
    chat_id = update.effective_chat.id
    loc = _locale(chat_id)

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(t(loc, "remind_usage"), parse_mode=ParseMode.HTML)
        return

    if context.job_queue is None:
        logger.error("cmd_remind: JobQueue non disponibile (manca l'extra python-telegram-bot[job-queue])")
        await update.message.reply_text(t(loc, "remind_unavailable"))
        return

    when_token = context.args[0]
    text = " ".join(context.args[1:]).strip()
    fire_at = _parse_when(when_token)
    if fire_at is None:
        await update.message.reply_text(t(loc, "remind_invalid_time"), parse_mode=ParseMode.HTML)
        return

    reminder_id = await reminder_repo.create(chat_id, user.id, text, fire_at)
    _schedule_reminder(context.job_queue, reminder_id, chat_id, text, fire_at)
    when_str = _fmt_when(fire_at)
    logger.info("cmd_remind: scheduled chat_id=%s fire_at=%s (in %ds) id=%s", chat_id, when_str, max(fire_at - int(time.time()), 0), reminder_id)
    await update.message.reply_text(
        t(loc, "remind_set", when=when_str, text=text, short_id=reminder_id[:8]),
        parse_mode=ParseMode.HTML,
    )


async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        return
    chat_id = update.effective_chat.id
    loc = _locale(chat_id)
    rows = await reminder_repo.list_pending(chat_id)
    if not rows:
        await update.message.reply_text(t(loc, "reminders_none"))
        return
    lines = [t(loc, "reminders_header")]
    for row in rows:
        lines.append(f"<code>{row['id'][:8]}</code> — {_fmt_when(row['fire_at'])} — {row['text']}")
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
    rows = await reminder_repo.list_pending(chat_id)
    match = next((r for r in rows if r["id"].startswith(prefix)), None)
    if not match:
        await update.message.reply_text(t(loc, "unremind_not_found"))
        return

    await reminder_repo.delete(match["id"], chat_id)
    # Cancel the scheduled job if still pending
    if context.job_queue is not None:
        for job in context.job_queue.get_jobs_by_name(f"reminder:{match['id']}"):
            job.schedule_removal()
    logger.info("cmd_unremind: cancelled id=%s chat_id=%s", match["id"], chat_id)
    await update.message.reply_text(t(loc, "unremind_done"))


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


async def _load_locales() -> None:
    """Warm-start the locale cache from telegram_prefs at boot."""
    try:
        _locales.update(await prefs_repo.load_all())
        logger.info("_load_locales: %d preferenze lingua caricate", len(_locales))
    except Exception:
        logger.exception("_load_locales: caricamento preferenze lingua fallito")


async def _reload_reminders(app: Application) -> None:
    """Re-schedule pending reminders persisted across a restart."""
    if app.job_queue is None:
        logger.warning("_reload_reminders: JobQueue non disponibile, reminder disabilitati")
        return
    rows = await reminder_repo.list_all_pending()
    for row in rows:
        _schedule_reminder(app.job_queue, row["id"], row["chat_id"], row["text"], row["fire_at"])
    if rows:
        logger.info("_reload_reminders: %d promemoria ripristinati", len(rows))


# ── Bot lifecycle ─────────────────────────────────────────────────────────────

_BOT_COMMANDS = [
    BotCommand("agent", "Modalità agente (Multi-MCP orchestrator)"),
    BotCommand("chat", "Torna alla chat normale (/chat <id> per un modello)"),
    BotCommand("imagine", "Genera un'immagine (/imagine <prompt>)"),
    BotCommand("new", "Nuova conversazione"),
    BotCommand("model", "Scegli il modello (tastiera inline)"),
    BotCommand("models", "Lista modelli disponibili (/models <query>)"),
    BotCommand("history", "Mostra la conversazione corrente"),
    BotCommand("search", "Cerca nelle conversazioni (/search <testo>)"),
    BotCommand("stats", "Statistiche di utilizzo"),
    BotCommand("remind", "Imposta un promemoria (/remind 15:50 testo)"),
    BotCommand("reminders", "Mostra i promemoria in programma"),
    BotCommand("unremind", "Annulla un promemoria (/unremind <id>)"),
    BotCommand("lang", "Cambia lingua del bot / change bot language"),
    BotCommand("link", "Collega al profilo web"),
    BotCommand("unlink", "Scollega dal profilo web"),
    BotCommand("help", "Mostra l'elenco dei comandi"),
]


async def _post_init(app: Application) -> None:
    """Register the command menu so commands show up under the Telegram '/' button."""
    bot_info = await app.bot.get_me()
    logger.info("Bot avviato: @%s (id=%s) — %d comandi registrati", bot_info.username, bot_info.id, len(_BOT_COMMANDS))
    await app.bot.set_my_commands(_BOT_COMMANDS)
    await _load_locales()
    await _reload_reminders(app)


def build_application() -> Application:
    logger.info("build_application: costruzione bot con default_model=%s", _default_model())
    allowed = _allowed_users()
    if allowed:
        logger.info("build_application: accesso limitato a %d utenti", len(allowed))
    else:
        logger.warning("build_application: nessun filtro utenti — accesso aperto a tutti")
    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .build()
    )
    # Command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("agent", cmd_agent))
    app.add_handler(CommandHandler("chat", cmd_chat))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(CommandHandler("imagine", cmd_imagine))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("remind", cmd_remind))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(CommandHandler("unremind", cmd_unremind))
    app.add_handler(CommandHandler("lang", cmd_lang))
    app.add_handler(CommandHandler("link", cmd_link))
    app.add_handler(CommandHandler("unlink", cmd_unlink))
    # Callback query handlers (inline keyboards)
    app.add_handler(CallbackQueryHandler(_cb_model_provider, pattern=r"^mp:(?!__back__)"))
    app.add_handler(CallbackQueryHandler(_cb_model_back, pattern=r"^mp:__back__$"))
    app.add_handler(CallbackQueryHandler(_cb_model_select, pattern=r"^ms:\d+$"))
    app.add_handler(CallbackQueryHandler(_cb_quick_action, pattern=r"^qa:"))
    app.add_handler(CallbackQueryHandler(_cb_lang, pattern=r"^lang:"))
    # Inline query handler
    app.add_handler(InlineQueryHandler(handle_inline_query))
    # Message handlers
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app
