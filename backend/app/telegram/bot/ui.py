"""Static UI surfaces: the quick-action keyboard and the /command list.

They live in a leaf module because both the streaming path and the
handlers that own them need to read them without importing each other.

Extracted from the former single-file bot.py.
"""

import logging

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

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


# ── Bot lifecycle ─────────────────────────────────────────────────────────────

_BOT_COMMANDS = [
    BotCommand("agent", "Modalità agente (Multi-MCP orchestrator)"),
    BotCommand("chat", "Torna alla chat normale (/chat <id> per un modello)"),
    BotCommand("imagine", "Genera un'immagine (/imagine <prompt>)"),
    BotCommand("new", "Nuova conversazione"),
    BotCommand("model", "Scegli il modello (tastiera inline)"),
    BotCommand("models", "Lista modelli disponibili (/models <query>)"),
    BotCommand("history", "Conversazioni recenti (riprendi da web o Telegram)"),
    BotCommand("search", "Cerca nelle conversazioni (/search <testo>)"),
    BotCommand("stats", "Statistiche di utilizzo"),
    BotCommand("remind", "Imposta un promemoria (/remind 15:50 testo, every day 08:00 …)"),
    BotCommand("remindai", "Promemoria intelligente (esegue un prompt al momento dell'invio)"),
    BotCommand("reminders", "Mostra i promemoria in programma"),
    BotCommand("unremind", "Annulla un promemoria (/unremind <id>)"),
    BotCommand("memory", "Memoria personale (/memory on|off|list|del)"),
    BotCommand("kb", "Knowledge base (/kb list|del; invia un file con didascalia /kb)"),
    BotCommand("rag", "Attiva/disattiva la knowledge base (/rag on|off)"),
    BotCommand("tool", "Attiva/disattiva l'uso degli strumenti (/tool on|off)"),
    BotCommand("tools", "Strumenti/MCP: elenca gli strumenti disponibili"),
    BotCommand("notify", "Notifiche dal web (/notify on|off)"),
    BotCommand("lang", "Cambia lingua del bot / change bot language"),
    BotCommand("link", "Collega al profilo web"),
    BotCommand("unlink", "Scollega dal profilo web"),
    BotCommand("help", "Mostra l'elenco dei comandi"),
]
