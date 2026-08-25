"""Warm-start of the preference caches, the command list and
build_application(), which wires every handler together.

Extracted from the former single-file bot.py.
"""

import logging

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from app.core.config import settings
from app.db import telegram_prefs_repository as prefs_repo

from .account import cmd_link, cmd_unlink
from .chat_commands import (
    _cb_model_back,
    _cb_model_provider,
    _cb_model_select,
    cmd_agent,
    cmd_chat,
    cmd_model,
    cmd_models,
    cmd_new,
    cmd_start,
    cmd_stats,
)
from .history import _cb_resume, cmd_history, cmd_search, handle_inline_query
from .kb import cmd_kb
from .lang import _cb_lang, cmd_lang
from .media import cmd_imagine, handle_document, handle_photo, handle_voice
from .messages import handle_message
from .prefs import cmd_memory, cmd_notify, cmd_rag, cmd_tool, cmd_tools
from .quick_actions import _cb_quick_action
from .reminders import (
    _cb_reminder_delete,
    _cb_reminder_repeat,
    _cb_reminder_snooze,
    cmd_remind,
    cmd_remindai,
    cmd_reminders,
    cmd_unremind,
)
from .state import (
    set_application,
    _active_convs,
    _allowed_users,
    _default_model,
    _locales,
    _memory_prefs,
    _notify_prefs,
    _rag_prefs,
    _tools_prefs,
)
from .ui import _BOT_COMMANDS
from .workflows import (
    _cb_run_pick,
    _cb_telegram_ask,
    _cb_workflow_approval,
    _dispatch_workflow_command,
    cmd_run,
    register_workflow_bot_commands,
)

logger = logging.getLogger(__name__)

async def _load_memory_prefs() -> None:
    """Warm-start the per-chat memory toggle cache from telegram_prefs at boot."""
    try:
        _memory_prefs.update(await prefs_repo.load_all_memory())
        if _memory_prefs:
            logger.info("_load_memory_prefs: %d preferenze memoria caricate", len(_memory_prefs))
    except Exception:
        logger.exception("_load_memory_prefs: caricamento preferenze memoria fallito")


async def _load_rag_prefs() -> None:
    """Warm-start the per-chat RAG toggle cache from telegram_prefs at boot (Phase 21)."""
    try:
        _rag_prefs.update(await prefs_repo.load_all_rag())
        if _rag_prefs:
            logger.info("_load_rag_prefs: %d preferenze RAG caricate", len(_rag_prefs))
    except Exception:
        logger.exception("_load_rag_prefs: caricamento preferenze RAG fallito")


async def _load_tools_prefs() -> None:
    """Warm-start the per-chat tool-loop toggle cache from telegram_prefs (Phase 23.b)."""
    try:
        _tools_prefs.update(await prefs_repo.load_all_tools())
        if _tools_prefs:
            logger.info("_load_tools_prefs: %d preferenze strumenti caricate", len(_tools_prefs))
    except Exception:
        logger.exception("_load_tools_prefs: caricamento preferenze strumenti fallito")


async def _load_notify_prefs() -> None:
    """Warm-start the per-chat notification-mute cache from telegram_prefs (Phase 23.c)."""
    try:
        _notify_prefs.update(await prefs_repo.load_all_notify())
        if _notify_prefs:
            logger.info("_load_notify_prefs: %d preferenze notifiche caricate", len(_notify_prefs))
    except Exception:
        logger.exception("_load_notify_prefs: caricamento preferenze notifiche fallito")


async def _load_locales() -> None:
    """Warm-start the locale cache from telegram_prefs at boot."""
    try:
        _locales.update(await prefs_repo.load_all())
        logger.info("_load_locales: %d preferenze lingua caricate", len(_locales))
    except Exception:
        logger.exception("_load_locales: caricamento preferenze lingua fallito")


async def _load_active_convs() -> None:
    """Warm-start the per-chat active-conversation cache from telegram_prefs (Phase 23.a)."""
    try:
        _active_convs.update(await prefs_repo.load_all_active())
        if _active_convs:
            logger.info("_load_active_convs: %d conversazioni attive caricate", len(_active_convs))
    except Exception:
        logger.exception("_load_active_convs: caricamento conversazioni attive fallito")


async def _post_init(app: Application) -> None:
    """Register the command menu so commands show up under the Telegram '/' button."""
    bot_info = await app.bot.get_me()
    logger.info("Bot avviato: @%s (id=%s) — %d comandi registrati", bot_info.username, bot_info.id, len(_BOT_COMMANDS))
    await app.bot.set_my_commands(_BOT_COMMANDS)
    await register_workflow_bot_commands(app)  # fase 20.5 — bound commands in the '/' menu
    await _load_locales()
    await _load_memory_prefs()
    await _load_rag_prefs()
    await _load_tools_prefs()
    await _load_notify_prefs()
    await _load_active_convs()


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
    app.add_handler(CommandHandler("remindai", cmd_remindai))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(CommandHandler("unremind", cmd_unremind))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("kb", cmd_kb))
    app.add_handler(CommandHandler("rag", cmd_rag))
    app.add_handler(CommandHandler("tool", cmd_tool))
    app.add_handler(CommandHandler("tools", cmd_tools))
    app.add_handler(CommandHandler("notify", cmd_notify))
    app.add_handler(CommandHandler("lang", cmd_lang))
    app.add_handler(CommandHandler("link", cmd_link))
    app.add_handler(CommandHandler("unlink", cmd_unlink))
    app.add_handler(CommandHandler("run", cmd_run))  # fase 20.1 — workflow launcher
    # Callback query handlers (inline keyboards)
    app.add_handler(CallbackQueryHandler(_cb_model_provider, pattern=r"^mp:(?!__back__)"))
    app.add_handler(CallbackQueryHandler(_cb_model_back, pattern=r"^mp:__back__$"))
    app.add_handler(CallbackQueryHandler(_cb_model_select, pattern=r"^ms:\d+$"))
    app.add_handler(CallbackQueryHandler(_cb_quick_action, pattern=r"^qa:"))
    app.add_handler(CallbackQueryHandler(_cb_resume, pattern=r"^resume:"))
    app.add_handler(CallbackQueryHandler(_cb_lang, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(_cb_reminder_snooze, pattern=r"^remind_snooze:"))
    app.add_handler(CallbackQueryHandler(_cb_reminder_repeat, pattern=r"^remind_repeat:"))
    app.add_handler(CallbackQueryHandler(_cb_reminder_delete, pattern=r"^remind_delete:"))
    app.add_handler(CallbackQueryHandler(_cb_workflow_approval, pattern=r"^wfap:[ar]:"))
    app.add_handler(CallbackQueryHandler(_cb_telegram_ask, pattern=r"^wfask:"))  # fase 20.3
    app.add_handler(CallbackQueryHandler(_cb_run_pick, pattern=r"^wfrun:"))       # fase 20.1
    # Inline query handler
    app.add_handler(InlineQueryHandler(handle_inline_query))
    # Message handlers
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # fase 20.1 — catch-all for a command bound to a workflow (registered last so
    # every builtin CommandHandler above wins; an unbound command is ignored).
    app.add_handler(MessageHandler(filters.COMMAND, _dispatch_workflow_command))
    set_application(app)
    return app
