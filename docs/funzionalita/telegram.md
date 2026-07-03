# Bot Telegram

**Cosa fa.** Bot in polling che espone il gateway su Telegram: cronologia per chat, risposte in streaming con modifica live del messaggio, selezione modello, vision, generazione immagini, trascrizione vocale, documenti, promemoria e collegamento al profilo web.

**Configurazione.** `TELEGRAM_BOT_TOKEN` in `backend/.env`; allowlist opzionale con `TELEGRAM_ALLOWED_USERS` (id utente separati da virgola). Fuso orario dei promemoria con `TIMEZONE` (default `Europe/Rome`). Richiede l'extra `python-telegram-bot[job-queue]` per i promemoria.

## Comandi

| Comando | Cosa fa |
|---------|---------|
| `/start` | messaggio di benvenuto |
| `/new` | nuova conversazione (azzera il contesto della chat) |
| `/model` | selezione modello con **tastiera inline a due passi** (provider → modello, con back e ✅ sul modello corrente) |
| `/models` | elenco modelli disponibili |
| `/agent` · `/chat` | commuta tra modalità agente (orchestratore Multi-MCP) e chat normale |
| `/imagine <prompt>` | genera un'immagine (catena `IMAGE_GENERATION_CHAIN`) e la invia come foto con caption provider/modello |
| `/history` | ultimi 20 messaggi della sessione corrente |
| `/search <query>` | ricerca full-text (FTS5) su tutte le conversazioni salvate: titoli + snippet |
| `/link` · `/unlink` | genera il codice per collegare/scollegare il profilo web (vedi [Autenticazione e profili](autenticazione-e-profili.md)) |
| `/remind` | promemoria: `/remind 15:50 Controlla i backup` oppure relativo `/remind +30m …`, `2h`, `1d` |
| `/reminders` · `/unremind <id>` | elenca / cancella i promemoria pendenti |
| `/lang` · `/lang en\|it` | lingua dell'interfaccia del bot per chat (tastiera inline o diretta); persistita in `telegram_prefs` |

## Contenuti multimediali

- **Foto** inviate al bot → descritte automaticamente dal modello attivo via vision.
- **Messaggi vocali/audio** → trascritti con Groq Whisper (`whisper-large-v3`); il bot mostra la trascrizione e poi risponde in streaming al testo trascritto.
- **Documenti** PDF / TXT / DOCX → il testo viene estratto (troncato a 8 000 caratteri) e usato come contesto per il modello, insieme all'eventuale caption.

## Quick action

Dopo ogni risposta compaiono pulsanti inline: **Regenerate** (riesegue l'ultimo turno), **Translate** (IT↔EN), **Summarize** (punti chiave), **Continue**.

## Modalità inline

`@nome_bot domanda` in qualunque chat Telegram: risposta diretta non-streaming (max 300 token) come `InlineQueryResultArticle`, con cache di 30 secondi.

## Promemoria persistenti

I promemoria sono salvati in `telegram_reminders` e schedulati sulla JobQueue di python-telegram-bot: **sopravvivono al riavvio** (ricaricati al boot). Gli orari usano `TIMEZONE`, indipendentemente dall'orologio del container.
