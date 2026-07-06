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
    "fr": "🇫🇷 Français",
    "de": "🇩🇪 Deutsch",
    "es": "🇪🇸 Español",
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
            "  /remind &lt;quando&gt; &lt;testo&gt; — promemoria (es. 15:50, +30m, ricorrenti)\n"
            "  /remindai &lt;quando&gt; &lt;prompt&gt; — promemoria intelligente\n"
            "  /reminders — promemoria in programma\n"
            "  /unremind &lt;id&gt; — annulla un promemoria\n"
            "  /memory — memoria personale (on|off|list|del)\n"
            "  /kb — knowledge base (list|del; file con didascalia /kb)\n"
            "  /rag — attiva/disattiva la knowledge base (on|off)\n"
            "  /tools — strumenti e MCP (elenca, on|off)\n"
            "  /lang — cambia lingua del bot\n"
            "  /link — collega al profilo web\n"
            "  /unlink — scollega dal profilo web\n"
            "  /help — mostra l'elenco dei comandi\n\n"
            "📸 Invia una foto per usare la vision\n"
            "🎙️ Invia un vocale per trascriverlo e rispondere\n"
            "📄 Invia un file PDF, TXT, DOCX o MD per analizzarlo\n"
            "📚 Invia un file con didascalia <code>/kb</code> per aggiungerlo alla knowledge base\n"
            "✨ Usa <code>@botname query</code> in qualsiasi chat per risposte inline"
        ),
        "new_cleared": "✅ Nuova conversazione avviata.",
        "history_title": "📜 <b>Le tue conversazioni recenti</b>\nTocca per riprenderne una:",
        "history_empty": "📭 Nessuna conversazione salvata. Scrivi un messaggio per iniziarne una.",
        "history_empty_unlinked": "📭 Nessun messaggio nella conversazione corrente.\nUsa /search per cercare, oppure /link per sincronizzare la cronologia con il web.",
        "history_current_header": "📜 <b>Conversazione corrente</b>",
        "history_resumed": "▶️ Conversazione ripresa: <b>{title}</b>",
        "history_resume_gone": "⚠ Conversazione non più disponibile.",
        "lang_choose": "🌐 Scegli la lingua del bot:",
        "lang_set": "✅ Lingua impostata: {label}",
        "remind_usage": (
            "Uso: <code>/remind &lt;quando&gt; &lt;testo&gt;</code>\n"
            "Esempi:\n"
            "  <code>/remind 15:50 Chiama Mario</code>\n"
            "  <code>/remind +30m Controlla i backup</code>\n"
            "  <code>/remind 2h Riunione</code>\n"
            "  <code>/remind domani alle 9 Dentista</code>\n"
            "  <code>/remind every day 08:00 Prendi le vitamine</code>\n"
            "  <code>/remind every monday Riunione settimanale</code>\n"
            "  <code>/remind cron:0,8,*,*,1-5 Sveglia nei giorni feriali</code>"
        ),
        "remindai_usage": (
            "Uso: <code>/remindai &lt;quando&gt; &lt;prompt&gt;</code>\n"
            "Al momento dell'invio esegue il prompt (può usare RSS, meteo, knowledge base) "
            "e consegna il risultato invece di un testo statico.\n"
            "Esempio: <code>/remindai every day 08:00 riassumi i miei feed RSS</code>"
        ),
        "remind_invalid_time": (
            "⚠️ Orario non valido. Usa <code>HH:MM</code> (es. 15:50), "
            "un valore relativo (es. <code>+30m</code>, <code>2h</code>, <code>1d</code>) "
            "o una frase come <code>domani alle 9</code>."
        ),
        "remind_unavailable": "⚠️ I promemoria non sono disponibili: lo scheduler non è attivo sul server.",
        "remind_set": (
            "⏰ Promemoria impostato per <b>{when}</b>:\n{text}\n\n"
            "<code>/reminders</code> per vederli, <code>/unremind {short_id}</code> per annullare."
        ),
        "remind_fired": "⏰ Promemoria: {text}",
        "remind_snoozed": "💤 Promemoria posticipato di 10 minuti.",
        "remind_deleted": "🗑 Promemoria eliminato.",
        "reminders_none": "Nessun promemoria in programma.",
        "reminders_header": "⏰ <b>Promemoria in programma:</b>\n",
        "unremind_usage": "Uso: <code>/unremind &lt;id&gt;</code>",
        "unremind_not_found": "⚠️ Nessun promemoria corrisponde a quell'id.",
        "unremind_done": "✅ Promemoria annullato.",
        "memory_usage": (
            "Uso: <code>/memory on|off|list|del &lt;id&gt;</code>\n"
            "  on/off — attiva/disattiva la memoria in questa chat\n"
            "  list — mostra i ricordi del profilo collegato\n"
            "  del &lt;id&gt; — dimentica un ricordo"
        ),
        "memory_on": "🧠 Memoria attivata per questa chat.",
        "memory_off": "🧠 Memoria disattivata per questa chat.",
        "memory_not_linked": (
            "⚠️ Nessun profilo web collegato. Usa /link e incolla il codice "
            "nella sidebar web per gestire i ricordi."
        ),
        "memory_empty": "🧠 Nessun ricordo salvato per il profilo collegato.",
        "memory_header": "🧠 <b>Ricordi del profilo:</b>\n",
        "memory_deleted": "✅ Ricordo dimenticato.",
        "memory_not_found": "⚠️ Nessun ricordo corrisponde a quell'id.",
        "kb_usage": (
            "Uso: <code>/kb list|del &lt;id&gt;</code>\n"
            "  list — mostra i documenti della knowledge base\n"
            "  del &lt;id&gt; — rimuovi un documento\n"
            "Invia un file PDF/TXT/DOCX/MD con didascalia <code>/kb</code> per aggiungerlo."
        ),
        "kb_not_linked": (
            "⚠️ Nessun profilo web collegato. Usa /link e incolla il codice "
            "nella sidebar web per gestire la knowledge base."
        ),
        "kb_empty": "📚 Nessun documento nella knowledge base del profilo collegato.",
        "kb_header": "📚 <b>Knowledge base del profilo:</b>\n",
        "kb_deleted": "✅ Documento rimosso.",
        "kb_not_found": "⚠️ Nessun documento corrisponde a quell'id.",
        "kb_ingesting": "📚 Aggiunta alla knowledge base…",
        "kb_ingested": "✅ <b>{filename}</b> aggiunto alla knowledge base ({chunks} frammenti).",
        "kb_duplicate": "ℹ️ Documento già presente come <b>{filename}</b>.",
        "kb_ingest_failed": "⚠️ Ingestione fallita: {error}",
        "kb_del_usage": "Uso: <code>/kb del &lt;id&gt;</code>",
        "rag_usage": (
            "Uso: <code>/rag on|off</code>\n"
            "  on/off — attiva/disattiva la knowledge base in questa chat"
        ),
        "rag_on": "📚 Knowledge base attivata per questa chat.",
        "rag_off": "📚 Knowledge base disattivata per questa chat.",
        "rag_not_linked": (
            "⚠️ Nessun profilo web collegato. Usa /link e incolla il codice "
            "nella sidebar web per usare la knowledge base."
        ),
        "rag_sources_header": "\n\n📚 Fonti: {sources}",
        "tool_usage": (
            "Uso: <code>/tool on|off</code>\n"
            "  attiva/disattiva l'uso degli strumenti in questa chat\n"
            "  usa <code>/tools</code> per elencare gli strumenti disponibili"
        ),
        "tools_on": "🔧 Strumenti attivati per questa chat.",
        "tools_off": "🔧 Strumenti disattivati per questa chat.",
        "notify_usage": (
            "Uso: <code>/notify on|off</code>\n"
            "  attiva/disattiva le notifiche dal web per questa chat"
        ),
        "notify_on": "🔔 Notifiche dal web attivate per questa chat.",
        "notify_off": "🔕 Notifiche dal web disattivate per questa chat.",
        "tools_header": "🔧 Strumenti disponibili ({count}):",
        "tools_none": "🔧 Nessuno strumento disponibile.",
        "tools_status_on": "Stato attuale: <b>attivi</b> ✅",
        "tools_status_off": "Stato attuale: <b>disattivati</b> 🚫",
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
            "  /remind &lt;when&gt; &lt;text&gt; — set a reminder (e.g. 15:50, +30m, recurring)\n"
            "  /remindai &lt;when&gt; &lt;prompt&gt; — smart reminder\n"
            "  /reminders — scheduled reminders\n"
            "  /unremind &lt;id&gt; — cancel a reminder\n"
            "  /memory — personal memory (on|off|list|del)\n"
            "  /kb — knowledge base (list|del; file with a /kb caption)\n"
            "  /rag — toggle the knowledge base (on|off)\n"
            "  /tools — tools and MCP (list, on|off)\n"
            "  /lang — change the bot language\n"
            "  /link — link to web profile\n"
            "  /unlink — unlink from web profile\n"
            "  /help — show the command list\n\n"
            "📸 Send a photo to use vision\n"
            "🎙️ Send a voice message to transcribe and answer\n"
            "📄 Send a PDF, TXT, DOCX or MD file to analyze it\n"
            "📚 Send a file with a <code>/kb</code> caption to add it to the knowledge base\n"
            "✨ Use <code>@botname query</code> in any chat for inline answers"
        ),
        "new_cleared": "✅ New conversation started.",
        "history_title": "📜 <b>Your recent conversations</b>\nTap one to resume it:",
        "history_empty": "📭 No saved conversations yet. Send a message to start one.",
        "history_empty_unlinked": "📭 No messages in the current conversation.\nUse /search to look them up, or /link to sync history with the web app.",
        "history_current_header": "📜 <b>Current conversation</b>",
        "history_resumed": "▶️ Resumed conversation: <b>{title}</b>",
        "history_resume_gone": "⚠ Conversation no longer available.",
        "lang_choose": "🌐 Choose the bot language:",
        "lang_set": "✅ Language set: {label}",
        "remind_usage": (
            "Usage: <code>/remind &lt;when&gt; &lt;text&gt;</code>\n"
            "Examples:\n"
            "  <code>/remind 15:50 Call Mario</code>\n"
            "  <code>/remind +30m Check the backups</code>\n"
            "  <code>/remind 2h Meeting</code>\n"
            "  <code>/remind tomorrow at 9 Dentist</code>\n"
            "  <code>/remind every day 08:00 Take vitamins</code>\n"
            "  <code>/remind every monday Weekly meeting</code>\n"
            "  <code>/remind cron:0,8,*,*,1-5 Weekday alarm</code>"
        ),
        "remindai_usage": (
            "Usage: <code>/remindai &lt;when&gt; &lt;prompt&gt;</code>\n"
            "At fire time it runs the prompt (can use RSS, weather, knowledge base) "
            "and delivers the result instead of static text.\n"
            "Example: <code>/remindai every day 08:00 summarize my RSS feeds</code>"
        ),
        "remind_invalid_time": (
            "⚠️ Invalid time. Use <code>HH:MM</code> (e.g. 15:50), "
            "a relative value (e.g. <code>+30m</code>, <code>2h</code>, <code>1d</code>), "
            "or a phrase like <code>tomorrow at 9</code>."
        ),
        "remind_unavailable": "⚠️ Reminders are unavailable: the scheduler is not running on the server.",
        "remind_set": (
            "⏰ Reminder set for <b>{when}</b>:\n{text}\n\n"
            "<code>/reminders</code> to list them, <code>/unremind {short_id}</code> to cancel."
        ),
        "remind_fired": "⏰ Reminder: {text}",
        "remind_snoozed": "💤 Reminder snoozed for 10 minutes.",
        "remind_deleted": "🗑 Reminder deleted.",
        "reminders_none": "No reminders scheduled.",
        "reminders_header": "⏰ <b>Scheduled reminders:</b>\n",
        "unremind_usage": "Usage: <code>/unremind &lt;id&gt;</code>",
        "unremind_not_found": "⚠️ No reminder matches that id.",
        "unremind_done": "✅ Reminder cancelled.",
        "memory_usage": (
            "Usage: <code>/memory on|off|list|del &lt;id&gt;</code>\n"
            "  on/off — enable/disable memory in this chat\n"
            "  list — show the linked profile's memories\n"
            "  del &lt;id&gt; — forget a memory"
        ),
        "memory_on": "🧠 Memory enabled for this chat.",
        "memory_off": "🧠 Memory disabled for this chat.",
        "memory_not_linked": (
            "⚠️ No web profile linked. Use /link and paste the code in the "
            "web sidebar to manage memories."
        ),
        "memory_empty": "🧠 No memories saved for the linked profile.",
        "memory_header": "🧠 <b>Profile memories:</b>\n",
        "memory_deleted": "✅ Memory forgotten.",
        "memory_not_found": "⚠️ No memory matches that id.",
        "kb_usage": (
            "Usage: <code>/kb list|del &lt;id&gt;</code>\n"
            "  list — show the knowledge base documents\n"
            "  del &lt;id&gt; — remove a document\n"
            "Send a PDF/TXT/DOCX/MD file with a <code>/kb</code> caption to add it."
        ),
        "kb_not_linked": (
            "⚠️ No web profile linked. Use /link and paste the code in the "
            "web sidebar to manage the knowledge base."
        ),
        "kb_empty": "📚 No documents in the linked profile's knowledge base.",
        "kb_header": "📚 <b>Profile knowledge base:</b>\n",
        "kb_deleted": "✅ Document removed.",
        "kb_not_found": "⚠️ No document matches that id.",
        "kb_ingesting": "📚 Adding to the knowledge base…",
        "kb_ingested": "✅ <b>{filename}</b> added to the knowledge base ({chunks} chunks).",
        "kb_duplicate": "ℹ️ Document already present as <b>{filename}</b>.",
        "kb_ingest_failed": "⚠️ Ingestion failed: {error}",
        "kb_del_usage": "Usage: <code>/kb del &lt;id&gt;</code>",
        "rag_usage": (
            "Usage: <code>/rag on|off</code>\n"
            "  on/off — enable/disable the knowledge base in this chat"
        ),
        "rag_on": "📚 Knowledge base enabled for this chat.",
        "rag_off": "📚 Knowledge base disabled for this chat.",
        "rag_not_linked": (
            "⚠️ No web profile linked. Use /link and paste the code in the "
            "web sidebar to use the knowledge base."
        ),
        "rag_sources_header": "\n\n📚 Sources: {sources}",
        "tool_usage": (
            "Usage: <code>/tool on|off</code>\n"
            "  enable/disable tool use in this chat\n"
            "  use <code>/tools</code> to list the available tools"
        ),
        "tools_on": "🔧 Tools enabled for this chat.",
        "tools_off": "🔧 Tools disabled for this chat.",
        "notify_usage": (
            "Usage: <code>/notify on|off</code>\n"
            "  enable/disable web-triggered notifications for this chat"
        ),
        "notify_on": "🔔 Web notifications enabled for this chat.",
        "notify_off": "🔕 Web notifications disabled for this chat.",
        "tools_header": "🔧 Available tools ({count}):",
        "tools_none": "🔧 No tools available.",
        "tools_status_on": "Current status: <b>enabled</b> ✅",
        "tools_status_off": "Current status: <b>disabled</b> 🚫",
    },
    "fr": {
        "access_denied": "⛔ Accès non autorisé.",
        "start": (
            "👋 Bonjour ! Je suis SpiceSibyl.\n\n"
            "Modèle actif : <code>{model}</code>\n\n"
            "Commandes :\n"
            "  /agent — mode agent (orchestrateur Multi-MCP)\n"
            "  /chat — revenir au chat normal\n"
            "  /chat &lt;id&gt; — discuter avec un modèle précis\n"
            "  /imagine &lt;prompt&gt; — générer une image\n"
            "  /new — nouvelle conversation\n"
            "  /model — choisir un modèle (clavier intégré)\n"
            "  /model &lt;id&gt; — changer de modèle directement\n"
            "  /models — liste des modèles disponibles\n"
            "  /models &lt;requête&gt; — filtrer par fournisseur, capacité ou nom\n"
            "  /history — afficher la conversation en cours\n"
            "  /search &lt;texte&gt; — rechercher dans les conversations enregistrées\n"
            "  /stats — statistiques d'utilisation\n"
            "  /remind &lt;quand&gt; &lt;texte&gt; — rappel (ex. 15:50, +30m, récurrent)\n"
            "  /remindai &lt;quand&gt; &lt;prompt&gt; — rappel intelligent\n"
            "  /reminders — rappels programmés\n"
            "  /unremind &lt;id&gt; — annuler un rappel\n"
            "  /memory — mémoire personnelle (on|off|list|del)\n"
            "  /kb — base de connaissances (list|del ; fichier avec la légende /kb)\n"
            "  /rag — activer/désactiver la base de connaissances (on|off)\n"
            "  /tools — outils et MCP (lister, on|off)\n"
            "  /lang — changer la langue du bot\n"
            "  /link — associer au profil web\n"
            "  /unlink — dissocier du profil web\n"
            "  /help — afficher la liste des commandes\n\n"
            "📸 Envoyez une photo pour utiliser la vision\n"
            "🎙️ Envoyez un message vocal pour le transcrire et répondre\n"
            "📄 Envoyez un fichier PDF, TXT, DOCX ou MD pour l'analyser\n"
            "📚 Envoyez un fichier avec la légende <code>/kb</code> pour l'ajouter à la base de connaissances\n"
            "✨ Utilisez <code>@botname requête</code> dans n'importe quel chat pour des réponses en ligne"
        ),
        "new_cleared": "✅ Nouvelle conversation démarrée.",
        "history_title": "📜 <b>Vos conversations récentes</b>\nAppuyez pour en reprendre une :",
        "history_empty": "📭 Aucune conversation enregistrée. Envoyez un message pour en commencer une.",
        "history_empty_unlinked": "📭 Aucun message dans la conversation actuelle.\nUtilisez /search pour rechercher, ou /link pour synchroniser l'historique avec le web.",
        "history_current_header": "📜 <b>Conversation actuelle</b>",
        "history_resumed": "▶️ Conversation reprise : <b>{title}</b>",
        "history_resume_gone": "⚠ Conversation introuvable.",
        "lang_choose": "🌐 Choisissez la langue du bot :",
        "lang_set": "✅ Langue définie : {label}",
        "remind_usage": (
            "Usage : <code>/remind &lt;quand&gt; &lt;texte&gt;</code>\n"
            "Exemples :\n"
            "  <code>/remind 15:50 Appeler Marie</code>\n"
            "  <code>/remind +30m Vérifier les sauvegardes</code>\n"
            "  <code>/remind 2h Réunion</code>\n"
            "  <code>/remind tomorrow at 9 Dentiste</code>\n"
            "  <code>/remind every day 08:00 Prendre les vitamines</code>\n"
            "  <code>/remind every monday Réunion hebdomadaire</code>\n"
            "  <code>/remind cron:0,8,*,*,1-5 Alarme en semaine</code>"
        ),
        "remindai_usage": (
            "Usage : <code>/remindai &lt;quand&gt; &lt;prompt&gt;</code>\n"
            "Au déclenchement, exécute le prompt (RSS, météo, base de connaissances) "
            "et livre le résultat au lieu d'un texte statique.\n"
            "Exemple : <code>/remindai every day 08:00 résume mes flux RSS</code>"
        ),
        "remind_invalid_time": (
            "⚠️ Heure invalide. Utilisez <code>HH:MM</code> (ex. 15:50), "
            "une valeur relative (ex. <code>+30m</code>, <code>2h</code>, <code>1d</code>) "
            "ou une phrase comme <code>tomorrow at 9</code>."
        ),
        "remind_unavailable": "⚠️ Les rappels sont indisponibles : le planificateur n'est pas actif sur le serveur.",
        "remind_set": (
            "⏰ Rappel programmé pour <b>{when}</b> :\n{text}\n\n"
            "<code>/reminders</code> pour les voir, <code>/unremind {short_id}</code> pour annuler."
        ),
        "remind_fired": "⏰ Rappel : {text}",
        "remind_snoozed": "💤 Rappel reporté de 10 minutes.",
        "remind_deleted": "🗑 Rappel supprimé.",
        "reminders_none": "Aucun rappel programmé.",
        "reminders_header": "⏰ <b>Rappels programmés :</b>\n",
        "unremind_usage": "Usage : <code>/unremind &lt;id&gt;</code>",
        "unremind_not_found": "⚠️ Aucun rappel ne correspond à cet id.",
        "unremind_done": "✅ Rappel annulé.",
        "memory_usage": (
            "Usage : <code>/memory on|off|list|del &lt;id&gt;</code>\n"
            "  on/off — activer/désactiver la mémoire dans ce chat\n"
            "  list — afficher les souvenirs du profil associé\n"
            "  del &lt;id&gt; — oublier un souvenir"
        ),
        "memory_on": "🧠 Mémoire activée pour ce chat.",
        "memory_off": "🧠 Mémoire désactivée pour ce chat.",
        "memory_not_linked": (
            "⚠️ Aucun profil web associé. Utilisez /link et collez le code "
            "dans la barre latérale web pour gérer les souvenirs."
        ),
        "memory_empty": "🧠 Aucun souvenir enregistré pour le profil associé.",
        "memory_header": "🧠 <b>Souvenirs du profil :</b>\n",
        "memory_deleted": "✅ Souvenir oublié.",
        "memory_not_found": "⚠️ Aucun souvenir ne correspond à cet id.",
        "kb_usage": (
            "Usage : <code>/kb list|del &lt;id&gt;</code>\n"
            "  list — afficher les documents de la base de connaissances\n"
            "  del &lt;id&gt; — supprimer un document\n"
            "Envoyez un fichier PDF/TXT/DOCX/MD avec la légende <code>/kb</code> pour l'ajouter."
        ),
        "kb_not_linked": (
            "⚠️ Aucun profil web associé. Utilisez /link et collez le code "
            "dans la barre latérale web pour gérer la base de connaissances."
        ),
        "kb_empty": "📚 Aucun document dans la base de connaissances du profil associé.",
        "kb_header": "📚 <b>Base de connaissances du profil :</b>\n",
        "kb_deleted": "✅ Document supprimé.",
        "kb_not_found": "⚠️ Aucun document ne correspond à cet id.",
        "kb_ingesting": "📚 Ajout à la base de connaissances…",
        "kb_ingested": "✅ <b>{filename}</b> ajouté à la base de connaissances ({chunks} fragments).",
        "kb_duplicate": "ℹ️ Document déjà présent sous le nom <b>{filename}</b>.",
        "kb_ingest_failed": "⚠️ Échec de l'ingestion : {error}",
        "kb_del_usage": "Usage : <code>/kb del &lt;id&gt;</code>",
        "rag_usage": (
            "Usage : <code>/rag on|off</code>\n"
            "  on/off — activer/désactiver la base de connaissances dans ce chat"
        ),
        "rag_on": "📚 Base de connaissances activée pour ce chat.",
        "rag_off": "📚 Base de connaissances désactivée pour ce chat.",
        "rag_not_linked": (
            "⚠️ Aucun profil web associé. Utilisez /link et collez le code "
            "dans la barre latérale web pour utiliser la base de connaissances."
        ),
        "rag_sources_header": "\n\n📚 Sources : {sources}",
        "tool_usage": (
            "Usage : <code>/tool on|off</code>\n"
            "  activer/désactiver les outils dans ce chat\n"
            "  utilisez <code>/tools</code> pour lister les outils disponibles"
        ),
        "tools_on": "🔧 Outils activés pour ce chat.",
        "tools_off": "🔧 Outils désactivés pour ce chat.",
        "notify_usage": (
            "Usage : <code>/notify on|off</code>\n"
            "  active/désactive les notifications depuis le web pour ce chat"
        ),
        "notify_on": "🔔 Notifications depuis le web activées pour ce chat.",
        "notify_off": "🔕 Notifications depuis le web désactivées pour ce chat.",
        "tools_header": "🔧 Outils disponibles ({count}) :",
        "tools_none": "🔧 Aucun outil disponible.",
        "tools_status_on": "État actuel : <b>activés</b> ✅",
        "tools_status_off": "État actuel : <b>désactivés</b> 🚫",
    },
    "de": {
        "access_denied": "⛔ Zugriff nicht autorisiert.",
        "start": (
            "👋 Hallo! Ich bin SpiceSibyl.\n\n"
            "Aktives Modell: <code>{model}</code>\n\n"
            "Befehle:\n"
            "  /agent — Agentenmodus (Multi-MCP-Orchestrator)\n"
            "  /chat — zurück zum normalen Chat\n"
            "  /chat &lt;id&gt; — mit einem bestimmten Modell chatten\n"
            "  /imagine &lt;prompt&gt; — ein Bild erzeugen\n"
            "  /new — neue Unterhaltung\n"
            "  /model — Modell wählen (Inline-Tastatur)\n"
            "  /model &lt;id&gt; — Modell direkt wechseln\n"
            "  /models — verfügbare Modelle auflisten\n"
            "  /models &lt;suche&gt; — nach Anbieter, Fähigkeit oder Name filtern\n"
            "  /history — aktuelle Unterhaltung anzeigen\n"
            "  /search &lt;text&gt; — gespeicherte Unterhaltungen durchsuchen\n"
            "  /stats — Nutzungsstatistiken\n"
            "  /remind &lt;wann&gt; &lt;text&gt; — Erinnerung (z. B. 15:50, +30m, wiederkehrend)\n"
            "  /remindai &lt;wann&gt; &lt;prompt&gt; — intelligente Erinnerung\n"
            "  /reminders — geplante Erinnerungen\n"
            "  /unremind &lt;id&gt; — eine Erinnerung abbrechen\n"
            "  /memory — persönliches Gedächtnis (on|off|list|del)\n"
            "  /kb — Wissensdatenbank (list|del; Datei mit Bildunterschrift /kb)\n"
            "  /rag — Wissensdatenbank ein-/ausschalten (on|off)\n"
            "  /tools — Tools und MCP (auflisten, on|off)\n"
            "  /lang — Bot-Sprache ändern\n"
            "  /link — mit Web-Profil verknüpfen\n"
            "  /unlink — vom Web-Profil trennen\n"
            "  /help — die Befehlsliste anzeigen\n\n"
            "📸 Sende ein Foto, um die Bilderkennung zu nutzen\n"
            "🎙️ Sende eine Sprachnachricht zum Transkribieren und Antworten\n"
            "📄 Sende eine PDF-, TXT-, DOCX- oder MD-Datei zur Analyse\n"
            "📚 Sende eine Datei mit der Bildunterschrift <code>/kb</code>, um sie zur Wissensdatenbank hinzuzufügen\n"
            "✨ Nutze <code>@botname Anfrage</code> in jedem Chat für Inline-Antworten"
        ),
        "new_cleared": "✅ Neue Unterhaltung gestartet.",
        "history_title": "📜 <b>Deine letzten Unterhaltungen</b>\nTippe, um eine fortzusetzen:",
        "history_empty": "📭 Noch keine gespeicherten Unterhaltungen. Sende eine Nachricht, um eine zu starten.",
        "history_empty_unlinked": "📭 Keine Nachrichten in der aktuellen Unterhaltung.\nNutze /search zum Suchen oder /link, um den Verlauf mit dem Web zu synchronisieren.",
        "history_current_header": "📜 <b>Aktuelle Unterhaltung</b>",
        "history_resumed": "▶️ Unterhaltung fortgesetzt: <b>{title}</b>",
        "history_resume_gone": "⚠ Unterhaltung nicht mehr verfügbar.",
        "lang_choose": "🌐 Wähle die Bot-Sprache:",
        "lang_set": "✅ Sprache eingestellt: {label}",
        "remind_usage": (
            "Verwendung: <code>/remind &lt;wann&gt; &lt;text&gt;</code>\n"
            "Beispiele:\n"
            "  <code>/remind 15:50 Maria anrufen</code>\n"
            "  <code>/remind +30m Backups prüfen</code>\n"
            "  <code>/remind 2h Besprechung</code>\n"
            "  <code>/remind tomorrow at 9 Zahnarzt</code>\n"
            "  <code>/remind every day 08:00 Vitamine nehmen</code>\n"
            "  <code>/remind every monday Wöchentliches Meeting</code>\n"
            "  <code>/remind cron:0,8,*,*,1-5 Wochentags-Wecker</code>"
        ),
        "remindai_usage": (
            "Verwendung: <code>/remindai &lt;wann&gt; &lt;prompt&gt;</code>\n"
            "Führt beim Auslösen den Prompt aus (RSS, Wetter, Wissensdatenbank) "
            "und liefert das Ergebnis statt eines statischen Textes.\n"
            "Beispiel: <code>/remindai every day 08:00 fasse meine RSS-Feeds zusammen</code>"
        ),
        "remind_invalid_time": (
            "⚠️ Ungültige Uhrzeit. Nutze <code>HH:MM</code> (z. B. 15:50), "
            "einen relativen Wert (z. B. <code>+30m</code>, <code>2h</code>, <code>1d</code>) "
            "oder eine Formulierung wie <code>tomorrow at 9</code>."
        ),
        "remind_unavailable": "⚠️ Erinnerungen sind nicht verfügbar: Der Planer läuft nicht auf dem Server.",
        "remind_set": (
            "⏰ Erinnerung gesetzt für <b>{when}</b>:\n{text}\n\n"
            "<code>/reminders</code> zum Anzeigen, <code>/unremind {short_id}</code> zum Abbrechen."
        ),
        "remind_fired": "⏰ Erinnerung: {text}",
        "remind_snoozed": "💤 Erinnerung um 10 Minuten verschoben.",
        "remind_deleted": "🗑 Erinnerung gelöscht.",
        "reminders_none": "Keine geplanten Erinnerungen.",
        "reminders_header": "⏰ <b>Geplante Erinnerungen:</b>\n",
        "unremind_usage": "Verwendung: <code>/unremind &lt;id&gt;</code>",
        "unremind_not_found": "⚠️ Keine Erinnerung entspricht dieser id.",
        "unremind_done": "✅ Erinnerung abgebrochen.",
        "memory_usage": (
            "Verwendung: <code>/memory on|off|list|del &lt;id&gt;</code>\n"
            "  on/off — Gedächtnis in diesem Chat ein-/ausschalten\n"
            "  list — die Erinnerungen des verknüpften Profils anzeigen\n"
            "  del &lt;id&gt; — eine Erinnerung vergessen"
        ),
        "memory_on": "🧠 Gedächtnis für diesen Chat aktiviert.",
        "memory_off": "🧠 Gedächtnis für diesen Chat deaktiviert.",
        "memory_not_linked": (
            "⚠️ Kein Web-Profil verknüpft. Nutze /link und füge den Code in der "
            "Web-Seitenleiste ein, um Erinnerungen zu verwalten."
        ),
        "memory_empty": "🧠 Keine Erinnerungen für das verknüpfte Profil gespeichert.",
        "memory_header": "🧠 <b>Profil-Erinnerungen:</b>\n",
        "memory_deleted": "✅ Erinnerung vergessen.",
        "memory_not_found": "⚠️ Keine Erinnerung entspricht dieser id.",
        "kb_usage": (
            "Verwendung: <code>/kb list|del &lt;id&gt;</code>\n"
            "  list — die Dokumente der Wissensdatenbank anzeigen\n"
            "  del &lt;id&gt; — ein Dokument entfernen\n"
            "Sende eine PDF-/TXT-/DOCX-/MD-Datei mit der Bildunterschrift <code>/kb</code>, um sie hinzuzufügen."
        ),
        "kb_not_linked": (
            "⚠️ Kein Web-Profil verknüpft. Nutze /link und füge den Code in der "
            "Web-Seitenleiste ein, um die Wissensdatenbank zu verwalten."
        ),
        "kb_empty": "📚 Keine Dokumente in der Wissensdatenbank des verknüpften Profils.",
        "kb_header": "📚 <b>Wissensdatenbank des Profils:</b>\n",
        "kb_deleted": "✅ Dokument entfernt.",
        "kb_not_found": "⚠️ Kein Dokument entspricht dieser id.",
        "kb_ingesting": "📚 Wird zur Wissensdatenbank hinzugefügt…",
        "kb_ingested": "✅ <b>{filename}</b> zur Wissensdatenbank hinzugefügt ({chunks} Fragmente).",
        "kb_duplicate": "ℹ️ Dokument bereits vorhanden als <b>{filename}</b>.",
        "kb_ingest_failed": "⚠️ Aufnahme fehlgeschlagen: {error}",
        "kb_del_usage": "Verwendung: <code>/kb del &lt;id&gt;</code>",
        "rag_usage": (
            "Verwendung: <code>/rag on|off</code>\n"
            "  on/off — die Wissensdatenbank in diesem Chat ein-/ausschalten"
        ),
        "rag_on": "📚 Wissensdatenbank für diesen Chat aktiviert.",
        "rag_off": "📚 Wissensdatenbank für diesen Chat deaktiviert.",
        "rag_not_linked": (
            "⚠️ Kein Web-Profil verknüpft. Nutze /link und füge den Code in der "
            "Web-Seitenleiste ein, um die Wissensdatenbank zu nutzen."
        ),
        "rag_sources_header": "\n\n📚 Quellen: {sources}",
        "tool_usage": (
            "Verwendung: <code>/tool on|off</code>\n"
            "  Tools in diesem Chat ein-/ausschalten\n"
            "  nutze <code>/tools</code>, um die verfügbaren Tools aufzulisten"
        ),
        "tools_on": "🔧 Tools für diesen Chat aktiviert.",
        "tools_off": "🔧 Tools für diesen Chat deaktiviert.",
        "notify_usage": (
            "Verwendung: <code>/notify on|off</code>\n"
            "  Web-Benachrichtigungen für diesen Chat aktivieren/deaktivieren"
        ),
        "notify_on": "🔔 Web-Benachrichtigungen für diesen Chat aktiviert.",
        "notify_off": "🔕 Web-Benachrichtigungen für diesen Chat deaktiviert.",
        "tools_header": "🔧 Verfügbare Tools ({count}):",
        "tools_none": "🔧 Keine Tools verfügbar.",
        "tools_status_on": "Aktueller Status: <b>aktiv</b> ✅",
        "tools_status_off": "Aktueller Status: <b>deaktiviert</b> 🚫",
    },
    "es": {
        "access_denied": "⛔ Acceso no autorizado.",
        "start": (
            "👋 ¡Hola! Soy SpiceSibyl.\n\n"
            "Modelo activo: <code>{model}</code>\n\n"
            "Comandos:\n"
            "  /agent — modo agente (orquestador Multi-MCP)\n"
            "  /chat — volver al chat normal\n"
            "  /chat &lt;id&gt; — chatear con un modelo específico\n"
            "  /imagine &lt;prompt&gt; — generar una imagen\n"
            "  /new — nueva conversación\n"
            "  /model — elegir modelo (teclado en línea)\n"
            "  /model &lt;id&gt; — cambiar de modelo directamente\n"
            "  /models — lista de modelos disponibles\n"
            "  /models &lt;consulta&gt; — filtrar por proveedor, capacidad o nombre\n"
            "  /history — mostrar la conversación actual\n"
            "  /search &lt;texto&gt; — buscar en las conversaciones guardadas\n"
            "  /stats — estadísticas de uso\n"
            "  /remind &lt;cuándo&gt; &lt;texto&gt; — recordatorio (p. ej. 15:50, +30m, recurrente)\n"
            "  /remindai &lt;cuándo&gt; &lt;prompt&gt; — recordatorio inteligente\n"
            "  /reminders — recordatorios programados\n"
            "  /unremind &lt;id&gt; — cancelar un recordatorio\n"
            "  /memory — memoria personal (on|off|list|del)\n"
            "  /kb — base de conocimiento (list|del; archivo con el pie /kb)\n"
            "  /rag — activar/desactivar la base de conocimiento (on|off)\n"
            "  /tools — herramientas y MCP (listar, on|off)\n"
            "  /lang — cambiar el idioma del bot\n"
            "  /link — vincular al perfil web\n"
            "  /unlink — desvincular del perfil web\n"
            "  /help — mostrar la lista de comandos\n\n"
            "📸 Envía una foto para usar la visión\n"
            "🎙️ Envía un mensaje de voz para transcribirlo y responder\n"
            "📄 Envía un archivo PDF, TXT, DOCX o MD para analizarlo\n"
            "📚 Envía un archivo con el pie <code>/kb</code> para añadirlo a la base de conocimiento\n"
            "✨ Usa <code>@botname consulta</code> en cualquier chat para respuestas en línea"
        ),
        "new_cleared": "✅ Nueva conversación iniciada.",
        "history_title": "📜 <b>Tus conversaciones recientes</b>\nToca una para retomarla:",
        "history_empty": "📭 Aún no hay conversaciones guardadas. Envía un mensaje para empezar una.",
        "history_empty_unlinked": "📭 No hay mensajes en la conversación actual.\nUsa /search para buscar, o /link para sincronizar el historial con la web.",
        "history_current_header": "📜 <b>Conversación actual</b>",
        "history_resumed": "▶️ Conversación retomada: <b>{title}</b>",
        "history_resume_gone": "⚠ La conversación ya no está disponible.",
        "lang_choose": "🌐 Elige el idioma del bot:",
        "lang_set": "✅ Idioma establecido: {label}",
        "remind_usage": (
            "Uso: <code>/remind &lt;cuándo&gt; &lt;texto&gt;</code>\n"
            "Ejemplos:\n"
            "  <code>/remind 15:50 Llamar a María</code>\n"
            "  <code>/remind +30m Revisar las copias de seguridad</code>\n"
            "  <code>/remind 2h Reunión</code>\n"
            "  <code>/remind tomorrow at 9 Dentista</code>\n"
            "  <code>/remind every day 08:00 Tomar vitaminas</code>\n"
            "  <code>/remind every monday Reunión semanal</code>\n"
            "  <code>/remind cron:0,8,*,*,1-5 Alarma entre semana</code>"
        ),
        "remindai_usage": (
            "Uso: <code>/remindai &lt;cuándo&gt; &lt;prompt&gt;</code>\n"
            "Al dispararse ejecuta el prompt (RSS, clima, base de conocimiento) "
            "y entrega el resultado en lugar de un texto estático.\n"
            "Ejemplo: <code>/remindai every day 08:00 resume mis feeds RSS</code>"
        ),
        "remind_invalid_time": (
            "⚠️ Hora no válida. Usa <code>HH:MM</code> (p. ej. 15:50), "
            "un valor relativo (p. ej. <code>+30m</code>, <code>2h</code>, <code>1d</code>) "
            "o una frase como <code>tomorrow at 9</code>."
        ),
        "remind_unavailable": "⚠️ Los recordatorios no están disponibles: el planificador no está activo en el servidor.",
        "remind_set": (
            "⏰ Recordatorio programado para <b>{when}</b>:\n{text}\n\n"
            "<code>/reminders</code> para verlos, <code>/unremind {short_id}</code> para cancelar."
        ),
        "remind_fired": "⏰ Recordatorio: {text}",
        "remind_snoozed": "💤 Recordatorio pospuesto 10 minutos.",
        "remind_deleted": "🗑 Recordatorio eliminado.",
        "reminders_none": "No hay recordatorios programados.",
        "reminders_header": "⏰ <b>Recordatorios programados:</b>\n",
        "unremind_usage": "Uso: <code>/unremind &lt;id&gt;</code>",
        "unremind_not_found": "⚠️ Ningún recordatorio coincide con ese id.",
        "unremind_done": "✅ Recordatorio cancelado.",
        "memory_usage": (
            "Uso: <code>/memory on|off|list|del &lt;id&gt;</code>\n"
            "  on/off — activar/desactivar la memoria en este chat\n"
            "  list — mostrar los recuerdos del perfil vinculado\n"
            "  del &lt;id&gt; — olvidar un recuerdo"
        ),
        "memory_on": "🧠 Memoria activada para este chat.",
        "memory_off": "🧠 Memoria desactivada para este chat.",
        "memory_not_linked": (
            "⚠️ Ningún perfil web vinculado. Usa /link y pega el código en la "
            "barra lateral web para gestionar los recuerdos."
        ),
        "memory_empty": "🧠 No hay recuerdos guardados para el perfil vinculado.",
        "memory_header": "🧠 <b>Recuerdos del perfil:</b>\n",
        "memory_deleted": "✅ Recuerdo olvidado.",
        "memory_not_found": "⚠️ Ningún recuerdo coincide con ese id.",
        "kb_usage": (
            "Uso: <code>/kb list|del &lt;id&gt;</code>\n"
            "  list — mostrar los documentos de la base de conocimiento\n"
            "  del &lt;id&gt; — eliminar un documento\n"
            "Envía un archivo PDF/TXT/DOCX/MD con el pie <code>/kb</code> para añadirlo."
        ),
        "kb_not_linked": (
            "⚠️ Ningún perfil web vinculado. Usa /link y pega el código en la "
            "barra lateral web para gestionar la base de conocimiento."
        ),
        "kb_empty": "📚 No hay documentos en la base de conocimiento del perfil vinculado.",
        "kb_header": "📚 <b>Base de conocimiento del perfil:</b>\n",
        "kb_deleted": "✅ Documento eliminado.",
        "kb_not_found": "⚠️ Ningún documento coincide con ese id.",
        "kb_ingesting": "📚 Añadiendo a la base de conocimiento…",
        "kb_ingested": "✅ <b>{filename}</b> añadido a la base de conocimiento ({chunks} fragmentos).",
        "kb_duplicate": "ℹ️ Documento ya presente como <b>{filename}</b>.",
        "kb_ingest_failed": "⚠️ Error de ingesta: {error}",
        "kb_del_usage": "Uso: <code>/kb del &lt;id&gt;</code>",
        "rag_usage": (
            "Uso: <code>/rag on|off</code>\n"
            "  on/off — activar/desactivar la base de conocimiento en este chat"
        ),
        "rag_on": "📚 Base de conocimiento activada para este chat.",
        "rag_off": "📚 Base de conocimiento desactivada para este chat.",
        "rag_not_linked": (
            "⚠️ Ningún perfil web vinculado. Usa /link y pega el código en la "
            "barra lateral web para usar la base de conocimiento."
        ),
        "rag_sources_header": "\n\n📚 Fuentes: {sources}",
        "tool_usage": (
            "Uso: <code>/tool on|off</code>\n"
            "  activar/desactivar el uso de herramientas en este chat\n"
            "  usa <code>/tools</code> para listar las herramientas disponibles"
        ),
        "tools_on": "🔧 Herramientas activadas para este chat.",
        "tools_off": "🔧 Herramientas desactivadas para este chat.",
        "notify_usage": (
            "Uso: <code>/notify on|off</code>\n"
            "  activa/desactiva las notificaciones desde la web para este chat"
        ),
        "notify_on": "🔔 Notificaciones desde la web activadas para este chat.",
        "notify_off": "🔕 Notificaciones desde la web desactivadas para este chat.",
        "tools_header": "🔧 Herramientas disponibles ({count}):",
        "tools_none": "🔧 No hay herramientas disponibles.",
        "tools_status_on": "Estado actual: <b>activadas</b> ✅",
        "tools_status_off": "Estado actual: <b>desactivadas</b> 🚫",
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
