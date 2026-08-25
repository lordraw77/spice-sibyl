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
  /remind <when> <t>— schedule a reminder (HH:MM, +30m/2h/1d, NL phrases, or
                      "every day 08:00 …"/"every monday …"/"cron:…" for recurring)
  /remindai <when> <prompt> — smart reminder: runs a small tool loop at fire time
  /reminders        — list pending reminders; /unremind <id> cancels
  /lang             — switch the bot UI language per chat (it/en)
  Any text          — sent to the active model, reply streamed back
  Voice/audio       — transcribed via Whisper, then processed as text

Localized user-facing strings live in app/telegram/i18n.py.

Module layout (roadmap v2 § 3, P2 "esplodere telegram/bot.py"). This file used
to hold all 2.5k lines; it is now the package façade and every concern has its
own module:

  state          per-chat state, counters, access control, get_bot()
  conversations  hydration and persistence of linked-chat exchanges
  chat_commands  /start /new /model /agent /chat /models /stats
  account        /link /unlink and the linked-profile lookup
  prefs          /memory /rag /notify /tools /tool
  kb             /kb
  media          /imagine, photo, voice and document handlers
  ui             the quick-action keyboard and the /command list
  quick_actions  quick-action callbacks
  streaming      the shared streaming reply path
  messages       plain-text handler
  history        /history /search, resume, inline queries
  reminders      /remind /remindai /reminders /unremind
  workflows      Phase 52 — Telegram as a workflow channel
  lang           /lang
  lifecycle      warm-start, command registration, build_application()

Everything the rest of the app imported from `app.telegram.bot` is re-exported
here, so `from app.telegram import bot; bot.get_bot()` keeps working — and stays
patchable in tests, which is how notification_service is exercised.
"""

from .lifecycle import build_application, register_workflow_bot_commands
from .state import (
    _notify_prefs,
    _split,
    counters,
    get_bot,
    get_telegram_stats,
    is_notify_enabled,
    set_application,
)
from .streaming import _assemble_tool_defs, _stream_reply
from .workflows import run_telegram_workflow, save_inbound_telegram_file

__all__ = [
    "build_application",
    "counters",
    "get_bot",
    "get_telegram_stats",
    "is_notify_enabled",
    "register_workflow_bot_commands",
    "run_telegram_workflow",
    "save_inbound_telegram_file",
    "set_application",
]
