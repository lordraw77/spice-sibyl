"""Per-chat in-memory state, counters, access control and text splitting.

Extracted from the former single-file bot.py.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass

from app.core.config import settings
from app.telegram import i18n
from app.services.chat_service import ChatService

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

# Phase 19: chat_id → memory injection toggle (/memory on|off), warm-cached like _locales
_memory_prefs: dict[int, bool] = {}

# Phase 21: chat_id → RAG injection toggle (/rag on|off), warm-cached; OFF by default
_rag_prefs: dict[int, bool] = {}

# Phase 23.b: chat_id → tool-loop toggle (/tools on|off), warm-cached; OFF by default.
# When ON (and not in agent mode), completions run the server-side tool loop with
# the built-in + custom + MCP tools merged in, exactly as the web chat.
_tools_prefs: dict[int, bool] = {}

# Phase 23.c: chat_id → cross-channel notification mute (/notify on|off), warm-cached;
# ON by default. Consulted by notification_service before pushing a web→Telegram event.
_notify_prefs: dict[int, bool] = {}

# Reused for the server-side tool-execution loop (Phase 23.b): the bot shares the
# web chat's loop so tool behavior stays identical across channels.
_chat_service = ChatService()

# Phase 23.a: chat_id → active persisted conversation id (linked chats only).
# Warm-started from telegram_prefs at boot; a chat with an entry here streams its
# exchanges into a regular profile conversation instead of the in-memory _sessions
# buffer. Chats not present (or unlinked) fall back to in-memory history.
_active_convs: dict[int, str] = {}

# chat_ids whose _sessions buffer has already been hydrated from its persisted
# active conversation this process (avoids re-reading the DB on every message).
_hydrated: set[int] = set()

# The running Application, set by build_application — lets notification_service
# and reminder_service push messages via get_bot() from outside the bot module.
_application: "Application | None" = None


def _locale(chat_id: int) -> str:
    """Resolve the UI locale for a chat (synchronous, cache-backed)."""
    return _locales.get(chat_id, i18n.DEFAULT_LOCALE)

# Temporary link codes: code → {telegram_id, username, expires}
import secrets
_link_codes: dict[str, dict] = {}


def _is_agent_model(model: str) -> bool:
    return model.startswith("agent/")

# ── In-memory counters ───────────────────────────────────────────────────────

@dataclass
class _Counters:
    """Process-wide message counters, surfaced by /stats and GET /v1/stats.

    A mutable object rather than three module-level ints: the handlers that
    bump them live in different modules now, and rebinding a plain int with
    `global` would give each module its own copy.
    """

    received: int = 0
    sent: int = 0
    errors: int = 0


counters = _Counters()


def get_telegram_stats() -> dict:
    return {
        "active_chats": len([s for s in _sessions.values() if s]),
        "messages_received": counters.received,
        "messages_sent": counters.sent,
        "errors": counters.errors,
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


def set_application(app: "Application") -> None:
    """Publish the running Application so get_bot() can reach it.

    build_application() lives in lifecycle.py now, and a plain `global` there
    would rebind lifecycle's own name — this keeps the single copy here.
    """
    global _application
    _application = app


def get_bot():
    """Return the running Telegram Bot instance, or None if the bot isn't active.

    Lets notification_service (Phase 23.c) and reminder_service (Phase 23.d)
    push a web/reminder → Telegram message without importing the rest of this
    module.
    """
    return _application.bot if _application is not None else None


def is_notify_enabled(chat_id: int) -> bool:
    """Whether cross-channel notifications are muted for this chat (/notify off)."""
    return _notify_prefs.get(chat_id, True)
