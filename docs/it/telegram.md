# Bot Telegram

**Cosa fa.** Bot in polling che espone il gateway su Telegram: cronologia per chat, risposte in streaming con modifica live del messaggio, selezione modello, vision, generazione immagini, trascrizione vocale, documenti, memoria personale, **knowledge base (RAG)**, promemoria e collegamento al profilo web.

**Configurazione.** `TELEGRAM_BOT_TOKEN` in `backend/.env`; allowlist opzionale con `TELEGRAM_ALLOWED_USERS` (id utente separati da virgola). Fuso orario dei promemoria con `TIMEZONE` (default `Europe/Rome`). Richiede l'extra `python-telegram-bot[job-queue]` per i promemoria.

## Comandi

| Comando | Cosa fa |
|---------|---------|
| `/start` | messaggio di benvenuto |
| `/new` | avvia una nuova conversazione (utenti collegati: la conversazione persistita viene creata al messaggio successivo) |
| `/model` | selezione modello con **tastiera inline a due passi** (provider → modello, con back e ✅ sul modello corrente) |
| `/models` | elenco modelli disponibili |
| `/agent` · `/chat` | commuta tra modalità agente (orchestratore Multi-MCP) e chat normale |
| `/imagine <prompt>` | genera un'immagine (catena `IMAGE_GENERATION_CHAIN`) e la invia come foto con caption provider/modello |
| `/history` | **utenti collegati:** conversazioni recenti da entrambi i canali, con tastiera inline per riprenderne una; **non collegati:** ultimi 20 messaggi della sessione corrente |
| `/search <query>` | ricerca full-text (FTS5) su tutte le conversazioni salvate: titoli + snippet |
| `/link` · `/unlink` | genera il codice per collegare/scollegare il profilo web (vedi [Autenticazione e profili](autenticazione-e-profili.md)) |
| `/remind` | promemoria: `/remind 15:50 Controlla i backup` oppure relativo `/remind +30m …`, `2h`, `1d` |
| `/reminders` · `/unremind <id>` | elenca / cancella i promemoria pendenti |
| `/memory on\|off\|list\|del <id>` | memoria personale sul profilo collegato (vedi [Memoria e personalizzazione](memoria-e-personalizzazione.md)) |
| `/kb list\|del <id>` | gestisce la knowledge base del profilo collegato; per aggiungere un documento invia un file con **didascalia `/kb`** (vedi sotto) |
| `/rag on\|off` | attiva/disattiva l'iniezione della knowledge base in questa chat (per chat, **OFF di default**) |
| `/tool on\|off` | attiva/disattiva il tool loop per questa chat (per chat, **OFF di default**) |
| `/tools` | elenca gli strumenti disponibili (raggruppati) e lo stato attuale del toggle — solo visualizzazione, non modifica lo stato |
| `/lang` · `/lang en\|it` | lingua dell'interfaccia del bot per chat (tastiera inline o diretta); persistita in `telegram_prefs` |

## Contenuti multimediali

- **Foto** inviate al bot → descritte automaticamente dal modello attivo via vision.
- **Messaggi vocali/audio** → trascritti con Groq Whisper (`whisper-large-v3`); il bot mostra la trascrizione e poi risponde in streaming al testo trascritto.
- **Documenti** PDF / TXT / DOCX / MD → il testo viene estratto (troncato a 8 000 caratteri) e usato come contesto **one-shot** per il modello, insieme all'eventuale caption. Con la didascalia `/kb` il documento viene invece **ingerito nella knowledge base** (vedi sotto).

## Cronologia condivisa (Fase 23.a)

Con un **profilo web collegato** (`/link`), Telegram non è più una chat separata in memoria: ogni scambio viene persistito come normale conversazione del profilo, così la cronologia è condivisa tra i due canali.

- **Persistenza** — ogni turno riuscito (testo, voce, foto, documento) viene salvato nella *conversazione attiva* della chat, creata al primo messaggio con un titolo generato automaticamente. La conversazione è marcata `channel='telegram'` e compare nella **sidebar web con un badge ✈️**; viceversa, le conversazioni iniziate sul web possono essere riprese da Telegram.
- **`/history`** — elenca le conversazioni più recenti del profilo (entrambi i canali) come tastiera inline; toccane una per riprenderla (quella attiva è marcata ✅). Il contesto completo viene ricaricato per continuare da dove avevi lasciato — anche dopo un riavvio del bot.
- **`/new`** — scollega la conversazione attiva; il messaggio successivo ne avvia una nuova. Lo stesso avviene cambiando modello (`/model`) o modalità (`/agent` / `/chat`).
- **Chat non collegate** mantengono la sessione in memoria (ultimi 40 messaggi), senza sincronizzazione tra canali — usa `/link` per abilitarla.

Implementazione: `telegram_prefs.active_conversation_id` (in cache all'avvio), `conversation_repository.append_messages` e la nuova colonna `conversations.channel`.

## Knowledge base (RAG)

Estende la RAG del profilo web (vedi [Knowledge base (RAG)](knowledge-rag.md)) al canale Telegram. Richiede un **profilo web collegato** (`/link`): tutti i comandi `/kb`/`/rag` e gli upload con didascalia `/kb` chiedono di collegarsi quando manca il link.

- **Ingestione** — invia un file **PDF / TXT / DOCX / MD** con didascalia `/kb`: viene aggiunto alla knowledge base del profilo collegato riusando la stessa pipeline degli upload web (`rag_service.ingest`: estrazione → chunking → embedding), con rilevamento duplicati via hash sha256 dei byte.
- **Gestione** — `/kb list` mostra i documenti con icona di stato (✅ pronto · ⏳ in corso · ⚠️ errore), 🔗 per i documenti da URL e numero di frammenti; `/kb del <id>` rimuove un documento per prefisso dell'id.
- **Recupero** — con `/rag on`, ad ogni messaggio `_stream_reply` recupera i chunk più pertinenti (`rag_service.retrieve`, ricerca ibrida + eventuale rerank) e li inietta nell'ultimo messaggio utente; la risposta riporta un footer 📚 con le fonti (nomi file deduplicati). Il toggle è **per chat**, persistito in `telegram_prefs.rag` e ricaricato al boot.

## Strumenti e MCP (Phase 23.b)

Porta il **tool loop** della chat web su Telegram: con `/tool on` una completion non si limita più allo streaming — il bot unisce gli strumenti integrati, i **custom tool** del profilo collegato e ogni **strumento MCP** individuato (`mcp__<server>__<tool>`, vedi [MCP](mcp.md)) nella richiesta ed esegue il loop server-side condiviso (`ChatService._stream_with_tools`), così il comportamento è identico su entrambi i canali.

- **Toggle** — `/tool on|off` attiva/disattiva il tool loop direttamente. **Per chat**, **OFF di default**, persistito in `telegram_prefs.tools` e caricato al boot (come `/rag`). Gli strumenti legati al profilo (`kb_search`, `create_reminder`, custom tool) si risolvono sul profilo collegato.
- **Elenco** — `/tools` elenca gli strumenti disponibili raggruppati per tipo (🧩 integrati · 🔌 MCP · 🛠 custom) insieme allo stato attuale del toggle; è solo visualizzazione e non modifica lo stato (usa `/tool` per cambiarlo).
- **Progresso** — le chiamate agli strumenti compaiono in tempo reale nella risposta (⚙ *nome strumento* durante l'esecuzione, che diventa ✅ al risultato).
- **Discovery** — gli strumenti MCP vengono ri-sondati quando esegui `/tools` (o quando la cache è fredda) e memorizzati in `mcp_service`, così i messaggi normali non pagano la latenza del probe.
- **Modalità agente** — i modelli `agent/*` orchestrano i propri strumenti; il toggle `/tool` non si applica ad essi.

## Quick action

Dopo ogni risposta compaiono pulsanti inline: **Regenerate** (riesegue l'ultimo turno), **Translate** (IT↔EN), **Summarize** (punti chiave), **Continue**.

## Modalità inline

`@nome_bot domanda` in qualunque chat Telegram: risposta diretta non-streaming (max 300 token) come `InlineQueryResultArticle`, con cache di 30 secondi.

## Promemoria persistenti

I promemoria sono salvati in `telegram_reminders` e schedulati sulla JobQueue di python-telegram-bot: **sopravvivono al riavvio** (ricaricati al boot). Gli orari usano `TIMEZONE`, indipendentemente dall'orologio del container.
