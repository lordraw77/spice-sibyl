"""
Telegram bot internationalization.

MESSAGES[locale][key] holds the localized strings; t(locale, key, **kwargs)
formats one with a fallback chain locale -> default ('it') -> key.  The default
is Italian to preserve the bot's original behavior for existing users.

Supported locales are exposed via SUPPORTED_LOCALES (code -> display label).
Add a locale by extending both MESSAGES and SUPPORTED_LOCALES.
"""

import logging

logger = logging.getLogger(__name__)

DEFAULT_LOCALE = "it"

SUPPORTED_LOCALES = {
    "it": "🇮🇹 Italiano",
    "en": "🇬🇧 English",
}

MESSAGES: dict[str, dict[str, str]] = {
    "it": {
        "access_denied": "⛔ Accesso non autorizzato.",
        "start": (
            "👋 Ciao! Sono SpiceSibyl.\n\n"
            "Modello attivo: <code>{model}</code>\n\n"
            "Comandi:\n"
            "  /agent — modalità agente (Multi-MCP orchestrator)\n"
            "  /chat — torna alla chat normale\n"
            "  /chat &lt;id&gt; — chat con un modello specifico\n"
            "  /imagine &lt;prompt&gt; — genera un'immagine\n"
            "  /new — nuova conversazione\n"
            "  /model — scegli modello (tastiera inline)\n"
            "  /model &lt;id&gt; — cambia modello direttamente\n"
            "  /models — lista modelli disponibili\n"
            "  /models &lt;query&gt; — filtra per provider, capability o nome\n"
            "  /history — mostra conversazione corrente\n"
            "  /search &lt;testo&gt; — cerca nelle conversazioni salvate\n"
            "  /stats — statistiche di utilizzo\n"
            "  /remind &lt;quando&gt; &lt;testo&gt; — promemoria (es. 15:50 o +30m)\n"
            "  /reminders — promemoria in programma\n"
            "  /unremind &lt;id&gt; — annulla un promemoria\n"
            "  /lang — cambia lingua del bot\n\n"
            "📸 Invia una foto per usare la vision\n"
            "🎙️ Invia un vocale per trascriverlo e rispondere\n"
            "📄 Invia un file PDF, TXT o DOCX per analizzarlo\n"
            "✨ Usa <code>@botname query</code> in qualsiasi chat per risposte inline"
        ),
        "new_cleared": "✅ Conversazione azzerata.",
        "lang_choose": "🌐 Scegli la lingua del bot:",
        "lang_set": "✅ Lingua impostata: {label}",
        "remind_usage": (
            "Uso: <code>/remind &lt;quando&gt; &lt;testo&gt;</code>\n"
            "Esempi:\n"
            "  <code>/remind 15:50 Chiama Mario</code>\n"
            "  <code>/remind +30m Controlla i backup</code>\n"
            "  <code>/remind 2h Riunione</code>"
        ),
        "remind_invalid_time": (
            "⚠️ Orario non valido. Usa <code>HH:MM</code> (es. 15:50) "
            "o un valore relativo (es. <code>+30m</code>, <code>2h</code>, <code>1d</code>)."
        ),
        "remind_unavailable": "⚠️ I promemoria non sono disponibili: lo scheduler non è attivo sul server.",
        "remind_set": (
            "⏰ Promemoria impostato per <b>{when}</b>:\n{text}\n\n"
            "<code>/reminders</code> per vederli, <code>/unremind {short_id}</code> per annullare."
        ),
        "remind_fired": "⏰ Promemoria: {text}",
        "reminders_none": "Nessun promemoria in programma.",
        "reminders_header": "⏰ <b>Promemoria in programma:</b>\n",
        "unremind_usage": "Uso: <code>/unremind &lt;id&gt;</code>",
        "unremind_not_found": "⚠️ Nessun promemoria corrisponde a quell'id.",
        "unremind_done": "✅ Promemoria annullato.",
    },
    "en": {
        "access_denied": "⛔ Access not authorized.",
        "start": (
            "👋 Hi! I'm SpiceSibyl.\n\n"
            "Active model: <code>{model}</code>\n\n"
            "Commands:\n"
            "  /agent — agent mode (Multi-MCP orchestrator)\n"
            "  /chat — back to normal chat\n"
            "  /chat &lt;id&gt; — chat with a specific model\n"
            "  /imagine &lt;prompt&gt; — generate an image\n"
            "  /new — new conversation\n"
            "  /model — pick a model (inline keyboard)\n"
            "  /model &lt;id&gt; — switch model directly\n"
            "  /models — list available models\n"
            "  /models &lt;query&gt; — filter by provider, capability or name\n"
            "  /history — show current conversation\n"
            "  /search &lt;text&gt; — search saved conversations\n"
            "  /stats — usage statistics\n"
            "  /remind &lt;when&gt; &lt;text&gt; — set a reminder (e.g. 15:50 or +30m)\n"
            "  /reminders — scheduled reminders\n"
            "  /unremind &lt;id&gt; — cancel a reminder\n"
            "  /lang — change the bot language\n\n"
            "📸 Send a photo to use vision\n"
            "🎙️ Send a voice message to transcribe and answer\n"
            "📄 Send a PDF, TXT or DOCX file to analyze it\n"
            "✨ Use <code>@botname query</code> in any chat for inline answers"
        ),
        "new_cleared": "✅ Conversation cleared.",
        "lang_choose": "🌐 Choose the bot language:",
        "lang_set": "✅ Language set: {label}",
        "remind_usage": (
            "Usage: <code>/remind &lt;when&gt; &lt;text&gt;</code>\n"
            "Examples:\n"
            "  <code>/remind 15:50 Call Mario</code>\n"
            "  <code>/remind +30m Check the backups</code>\n"
            "  <code>/remind 2h Meeting</code>"
        ),
        "remind_invalid_time": (
            "⚠️ Invalid time. Use <code>HH:MM</code> (e.g. 15:50) "
            "or a relative value (e.g. <code>+30m</code>, <code>2h</code>, <code>1d</code>)."
        ),
        "remind_unavailable": "⚠️ Reminders are unavailable: the scheduler is not running on the server.",
        "remind_set": (
            "⏰ Reminder set for <b>{when}</b>:\n{text}\n\n"
            "<code>/reminders</code> to list them, <code>/unremind {short_id}</code> to cancel."
        ),
        "remind_fired": "⏰ Reminder: {text}",
        "reminders_none": "No reminders scheduled.",
        "reminders_header": "⏰ <b>Scheduled reminders:</b>\n",
        "unremind_usage": "Usage: <code>/unremind &lt;id&gt;</code>",
        "unremind_not_found": "⚠️ No reminder matches that id.",
        "unremind_done": "✅ Reminder cancelled.",
    },
}


def t(locale: str | None, key: str, **kwargs) -> str:
    """Return the localized string for key, formatted with kwargs.

    Falls back to the default locale, then to the key itself if missing.
    """
    table = MESSAGES.get(locale or DEFAULT_LOCALE) or MESSAGES[DEFAULT_LOCALE]
    template = table.get(key) or MESSAGES[DEFAULT_LOCALE].get(key) or key
    try:
        return template.format(**kwargs) if kwargs else template
    except (KeyError, IndexError):
        logger.warning("i18n: bad format for key=%s locale=%s", key, locale)
        return template
