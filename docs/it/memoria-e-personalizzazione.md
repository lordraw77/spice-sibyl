# Memoria e personalizzazione

Funzionalità della Fase 19: memoria persistente per profilo, titoli automatici, cache delle risposte, feedback sulle risposte e pagina Info.

## Memoria persistente per profilo

**Cosa fa.** SpiceSibyl ricorda i fatti su di te tra una conversazione e l'altra (preferenze, fatti personali, progetti in corso, istruzioni permanenti). Dopo ogni scambio salvato, una chiamata LLM asincrona a basso costo (`MEMORY_EXTRACTION_MODEL`, default = `DEFAULT_MODEL`) estrae le informazioni degne di nota e le consolida nella tabella `profile_memories` (dedup automatico, massimo `MEMORY_MAX_ITEMS` ricordi). Quando la memoria è attiva, i ricordi abilitati vengono compattati in un blocco `<user_memory>` aggiunto al system prompt (budget `MEMORY_MAX_CHARS` caratteri, più recenti per primi).

**Come si usa.**
- Pagina dedicata **Memoria 🧠** (`/memory`, voce **Risorse → Memoria** nella navbar, o link *Gestisci →* accanto all'interruttore Memoria in sidebar): elenco dei ricordi con categoria (⭐ preferenza, 💡 fatto, 📁 progetto, 📌 istruzione), aggiunta manuale con scelta della categoria, attiva/disattiva o elimina il singolo ricordo, **Dimentica tutto**. Qui c'è anche la checkbox **Estrazione automatica dei ricordi (profilo)** — interruttore *di profilo*: con OFF niente estrazione né iniezione per tutto il profilo.
- Il toggle **Memoria ON/OFF** nella sezione **Funzioni** della sidebar è l'interruttore *per-chat* (incognito): con OFF le nuove richieste non usano né alimentano la memoria.
- Quando una risposta è stata personalizzata con la memoria compare il chip **🧠 memoria** sotto il messaggio.

**Da Telegram.** `/memory on|off` attiva/disattiva la memoria nella chat corrente (persistito in `telegram_prefs`); `/memory list` mostra i ricordi del profilo web collegato via `/link`; `/memory del <id>` ne dimentica uno. L'iniezione e l'estrazione funzionano solo per gli utenti collegati.

**Configurazione.**

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `MEMORY_ENABLED` | `true` | Interruttore globale della funzionalità |
| `MEMORY_EXTRACTION_MODEL` | *(vuoto = `DEFAULT_MODEL`)* | Modello per l'estrazione asincrona |
| `MEMORY_MAX_CHARS` | `2000` | Budget caratteri del blocco iniettato |
| `MEMORY_MAX_ITEMS` | `100` | Massimo ricordi per profilo |

API: `GET/POST /v1/memories`, `PATCH/DELETE /v1/memories/{id}`, `DELETE /v1/memories` (dimentica tutto), `GET/PUT /v1/memories/settings`.

## Titoli automatici (LLM auto-titling)

**Cosa fa.** Dopo il primo scambio salvato di una conversazione, un task in background genera un titolo conciso (max 6 parole, nella lingua della conversazione) al posto della vecchia euristica "primi 60 caratteri del messaggio". La lista delle conversazioni (pannello Conversazioni) si aggiorna da sola pochi secondi dopo.

**Configurazione.** `AUTO_TITLE_ENABLED` (default `true`), `TITLE_MODEL` (vuoto = `MEMORY_EXTRACTION_MODEL`, poi `DEFAULT_MODEL`).

## Cache delle risposte

**Cosa fa.** Le risposte completate vengono messe in una cache LRU in-memory con chiave esatta su modello + messaggi + temperatura + max token. Una richiesta identica entro il TTL salta del tutto il provider: la risposta viene riprodotta in un colpo solo con il chip **⚡ cache** e latenza zero. Non vengono mai messe in cache le richieste con tool, i modelli `agent/*` e i contenuti multimodali (immagini).

**Configurazione.** `RESPONSE_CACHE_ENABLED` (default `true`), `RESPONSE_CACHE_TTL_SECONDS` (default `600`), `RESPONSE_CACHE_MAX_ENTRIES` (default `256`). Le statistiche hit/miss sono visibili nella pagina **Info**.

## Cache semantica delle risposte

**Cosa fa.** Estende la cache a corrispondenza esatta con una corrispondenza *approssimata*. In caso di miss esatto, l'ultimo messaggio utente viene trasformato in embedding (con la stessa catena di embedding usata per il RAG) e confrontato per similarità coseno con le risposte recenti in cache nello stesso bucket modello + temperatura + max token. Una corrispondenza pari o superiore alla soglia riproduce la risposta salvata con il chip **⚡~ cache** — così parafrasi come «Come reimposto la password?» e «Come posso reimpostare la password?» riusano un'unica risposta senza chiamare il provider. Valgono le stesse esclusioni (tool, `agent/*`, multimodale) e degrada silenziosamente alla sola corrispondenza esatta quando nessun provider di embedding è raggiungibile.

**Configurazione.** `SEMANTIC_CACHE_ENABLED` (default `false`), `SEMANTIC_CACHE_THRESHOLD` (coseno, default `0.92`), `SEMANTIC_CACHE_MAX_ENTRIES` (finestra di scansione, default `256`). I conteggi hit/miss semantici compaiono accanto a quelli esatti nelle statistiche cache della pagina **Info**.

## Feedback sulle risposte (👍/👎)

**Cosa fa.** Ogni risposta salvata dell'assistente può essere valutata con pollice su/giù (con nota opzionale sul 👎). Le valutazioni alimentano un dataset esportabile per la valutazione offline dei modelli.

**Come si usa.**
- Passa il mouse su una risposta: tra le azioni compaiono 👍 e 👎. Un secondo clic sulla stessa icona rimuove la valutazione.
- Esporta il dataset da `GET /v1/feedback/export`: ogni risposta valutata è accoppiata al prompt che l'ha generata (message id, modello, provider, rating, nota).
- Harness di regressione: `backend/scripts/eval_regression.py` riesegue i prompt approvati (👍) contro il gateway e segnala le risposte che si discostano troppo da quelle già approvate.

```bash
python backend/scripts/eval_regression.py dataset.json \
  --base-url http://localhost:8800/api/v1 \
  --email admin@example.com --password ... [--model groq/llama-3.1-8b-instant]
```

## Pagina Info

**Cosa fa.** La voce **Info** nella navbar apre una pagina con: versione della web UI (dal `package.json` di build), versione/ambiente/uptime del backend (`GET /v1/info`), modello di default, database (percorso e dimensione), endpoint API in uso (base URL, health, readiness, metriche, link ai docs OpenAPI), stato live READY/DEGRADED e l'elenco delle funzionalità attive con le statistiche della cache.

**Configurazione.** La versione del backend viene da `APP_VERSION` (default allineato al rilascio); nelle build Docker è impostata automaticamente dal tag di release (`make release VERSION=v1.9.0`).
