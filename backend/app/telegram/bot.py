"""
Telegram bot for SpiceSibyl.

Each Telegram chat gets its own in-memory conversation history and a chosen
model.  The bot streams the provider response and edits the reply message as
tokens arrive (throttled to ~1 edit/s to stay within Telegram rate limits).

Commands:
  /start            — welcome message
  /new              — clear conversation history for this chat
  /model            — show the current model
  /model <id>       — switch to a different model
  /models           — list all available models grouped by provider
  /models <query>   — filter models by provider, capability or name
  /stats            — show global usage statistics
  Any text          — sent to the active model, reply streamed back
"""

import asyncio
import logging
import time
from collections import defaultdict

import aiosqlite
from telegram import BotCommand, Update
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
from app.db.stats_repository import get_usage_stats
from app.dependencies.provider_factory import get_provider
from app.schemas.chat import ChatCompletionRequest, ChatMessage

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
    if not _is_allowed(update.effective_user.id):
        return
    model = _models.get(update.effective_chat.id, _default_model())
    await update.message.reply_text(
        f"👋 Ciao! Sono SpiceSibyl.\n\n"
        f"Modello attivo: <code>{model}</code>\n\n"
        f"Comandi:\n"
        f"  /agent — modalità agente (Multi-MCP orchestrator)\n"
        f"  /chat — torna alla chat normale\n"
        f"  /chat &lt;id&gt; — chat con un modello specifico\n"
        f"  /new — nuova conversazione\n"
        f"  /model — mostra modello corrente\n"
        f"  /model &lt;id&gt; — cambia modello\n"
        f"  /models — lista modelli disponibili\n"
        f"  /models &lt;query&gt; — filtra per provider, capability o nome\n"
        f"  /stats — statistiche di utilizzo",
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


async def cmd_agent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Switch this chat to the Multi-MCP orchestrator (agent mode)."""
    if not _is_allowed(update.effective_user.id):
        return
    chat_id = update.effective_chat.id
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
    if not _is_allowed(update.effective_user.id):
        return
    chat_id = update.effective_chat.id

    if context.args:
        target = context.args[0].strip()
    else:
        target = _chat_models.get(chat_id) or _default_model()
        if _is_agent_model(target):
            target = _default_model()

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
    if not _is_allowed(update.effective_user.id):
        return

    query = ' '.join(context.args).strip().lower() if context.args else ''
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
    if not _is_allowed(update.effective_user.id):
        return

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


# ── Message handler ──────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _tg_messages_received, _tg_messages_sent, _tg_errors

    if not update.message or not update.message.text:
        return
    if not _is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return

    _tg_messages_received += 1

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
    progress: list[str] = []  # transient sub-agent delegation status (agent/* models)
    last_edit = time.monotonic()

    try:
        async for chunk in provider.stream(request):
            if chunk.get("object") == "chat.completion.meta":
                break

            # Orchestrator progress frames — show delegation status until the
            # final answer starts streaming (agent/multi-mcp).
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
            _tg_messages_sent += 1

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _tg_errors += 1
        logger.exception("Telegram handler error for chat_id=%s model=%s", chat_id, model)
        try:
            await sent.edit_text(f"⚠ Errore: {exc}")
        except Exception:
            pass
        # Remove the user message so history stays consistent
        if session and session[-1]["role"] == "user":
            session.pop()


# ── Bot lifecycle ─────────────────────────────────────────────────────────────

# Command menu shown in the Telegram UI (the "/" hint list).
_BOT_COMMANDS = [
    BotCommand("agent", "Modalità agente (Multi-MCP orchestrator)"),
    BotCommand("chat", "Torna alla chat normale (/chat <id> per un modello)"),
    BotCommand("new", "Nuova conversazione"),
    BotCommand("model", "Mostra o cambia il modello (/model <id>)"),
    BotCommand("models", "Lista modelli disponibili (/models <query>)"),
    BotCommand("stats", "Statistiche di utilizzo"),
    BotCommand("help", "Mostra l'elenco dei comandi"),
]


async def _post_init(app: Application) -> None:
    """Register the command menu so commands show up under the Telegram '/' button."""
    await app.bot.set_my_commands(_BOT_COMMANDS)


def build_application() -> Application:
    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("agent", cmd_agent))
    app.add_handler(CommandHandler("chat", cmd_chat))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app
