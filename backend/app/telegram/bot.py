"""
Telegram bot for SpiceSibyl.

Each Telegram chat gets its own in-memory conversation history and a chosen
model.  The bot streams the provider response and edits the reply message as
tokens arrive (throttled to ~1 edit/s to stay within Telegram rate limits).

Commands:
  /start         — welcome message
  /new           — clear conversation history for this chat
  /model         — show the current model
  /model <id>    — switch to a different model
  /models        — list all available models
  Any text       — sent to the active model, reply streamed back
"""

import asyncio
import logging
import time
from collections import defaultdict

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.core.config import settings
from app.data.model_catalog import iter_configured_models
from app.dependencies.provider_factory import get_provider
from app.schemas.chat import ChatCompletionRequest, ChatMessage

logger = logging.getLogger(__name__)

# ── Session state ────────────────────────────────────────────────────────────

# chat_id → list of message dicts (role/content only, no telemetry)
_sessions: dict[int, list[dict]] = defaultdict(list)

# chat_id → model id string
_models: dict[int, str] = {}

_MAX_HISTORY = 40  # keep last 40 messages (~20 exchanges) per chat

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
    if not _is_allowed(update.effective_user.id):
        return
    model = _models.get(update.effective_chat.id, _default_model())
    await update.message.reply_text(
        f"👋 Ciao! Sono SpiceSibyl.\n\n"
        f"Modello attivo: <code>{model}</code>\n\n"
        f"Comandi:\n"
        f"  /new — nuova conversazione\n"
        f"  /model — mostra modello corrente\n"
        f"  /model &lt;id&gt; — cambia modello\n"
        f"  /models — lista modelli disponibili",
        parse_mode=ParseMode.HTML,
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        return
    _sessions[update.effective_chat.id].clear()
    await update.message.reply_text("✅ Conversazione azzerata.")


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        return
    chat_id = update.effective_chat.id
    args = context.args

    if not args:
        current = _models.get(chat_id, _default_model())
        await update.message.reply_text(
            f"Modello corrente: <code>{current}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    model_id = args[0].strip()
    _models[chat_id] = model_id
    _sessions[chat_id].clear()
    await update.message.reply_text(
        f"✅ Modello impostato: <code>{model_id}</code>\nConversazione azzerata.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_models(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        return
    models_by_provider: dict[str, list[str]] = defaultdict(list)
    for m in iter_configured_models():
        models_by_provider[m.get('provider', 'other')].append(m['id'])

    lines = []
    for provider, ids in sorted(models_by_provider.items()):
        lines.append(f"<b>{provider}</b>")
        for mid in ids[:10]:  # cap per-provider to keep message short
            lines.append(f"  <code>{mid}</code>")
        if len(ids) > 10:
            lines.append(f"  … +{len(ids) - 10} altri")

    text = '\n'.join(lines) or "Nessun modello disponibile."
    for chunk in _split(text):
        await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)


# ── Message handler ──────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    if not _is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return

    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    model = _models.get(chat_id, _default_model())

    # Append user message to history
    session = _sessions[chat_id]
    session.append({"role": "user", "content": text})

    # Trim to keep last _MAX_HISTORY messages
    if len(session) > _MAX_HISTORY:
        _sessions[chat_id] = session[-_MAX_HISTORY:]
        session = _sessions[chat_id]

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)

    # Send a placeholder that we'll edit as tokens arrive
    sent = await update.message.reply_text("⏳")

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

            # Throttle edits to ~1/s to avoid Telegram flood limits
            now = time.monotonic()
            if now - last_edit >= 1.0:
                try:
                    await sent.edit_text(full_content + " ▌")
                    last_edit = now
                except Exception:
                    pass  # edit may fail if content unchanged

        # Final message — send in chunks if long
        chunks = _split(full_content or "⚠ Nessuna risposta.")
        await sent.edit_text(chunks[0])
        for extra in chunks[1:]:
            await update.message.reply_text(extra)

        if full_content:
            session.append({"role": "assistant", "content": full_content})

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Telegram handler error for chat_id=%s model=%s", chat_id, model)
        try:
            await sent.edit_text(f"⚠ Errore: {exc}")
        except Exception:
            pass
        # Remove the user message so history stays consistent
        if session and session[-1]["role"] == "user":
            session.pop()


# ── Bot lifecycle ─────────────────────────────────────────────────────────────

def build_application() -> Application:
    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app
