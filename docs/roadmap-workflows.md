# Roadmap — Evoluzione Workflow Engine (post Phase 29)

Stato di partenza: motore DAG v2.2.0 (`/v1/graph-workflows`) con trigger `manual/schedule/webhook/event`, logica `if/switch/merge/for/repeat/filter/aggregate/batch/set/wait`, nodi `llm.completion/llm.agent`, `http.request`, `subworkflow`, `code`, notifiche `email/telegram/inapp/webhook`, 4 tool wrapper. Editor visuale Angular (canvas SVG) + pagine Runs e Schedules.

Le fasi sono ordinate per dipendenza: ogni fase abilita o semplifica le successive. All'interno di una fase gli item sono indipendenti e parallelizzabili.

---

## Fase 1 — Fondamenta (refactoring e infrastruttura) ✅ COMPLETATA (2026-07)

Obiettivo: preparare il terreno. Nessuna feature visibile all'utente, ma tutto ciò che segue costa meno dopo questa fase.

> **Stato:** tutti e quattro gli item sono implementati e testati.
> 1.1 — editor spacchettato in 6 componenti standalone sotto `frontend/src/app/features/workflows/editor/` (graph-canvas, node-palette, editor-toolbar, node-inspector, edge-inspector, run-panel); il page component è un orchestratore.
> 1.2 — shell `/graph-workflows/:id` con tab Editor | Runs | Schedules scoped sul workflow (`workflow-shell.component`); le pagine globali restano per la vista trasversale.
> 1.3 — `$vars` per workflow (colonna `variables_json`, PATCH `variables`, editor nel run panel) e `$secrets` di profilo cifrati Fernet con `VAULT_SECRET_KEY` (tabella `workflow_secrets`, endpoint GET/PUT/DELETE `/secrets`, mai restituiti in chiaro, mascherati in preview, esclusi dall'export).
> 1.4 — il versioning backend esisteva già (snapshot su save, restore); aggiunta la UI: sezione **Versioni** nel run panel con lista e Ripristina.
> Doc aggiornate in 5 lingue + frontend-overview + examples + `.env.example`; 6 nuovi test backend (63 totali del motore, verdi).

### 1.1 Refactoring UI dell'editor grafico
Spacchettare `graph-workflow-page.component` (oggi ~1.300 righe TS + ~500 HTML) in componenti standalone sotto `frontend/src/app/features/workflows/editor/`:

| Componente | Responsabilità |
|---|---|
| `graph-canvas.component` | Solo SVG: rendering nodi/edge, pan/zoom, drag, selezione. Comunica via `@Input`/`@Output` (`nodeMoved`, `edgeCreated`, `nodeSelected`), non tocca i service |
| `node-palette.component` | Catalogo nodi trascinabili, alimentato da `node_catalog()` |
| `node-inspector.component` | Pannello proprietà del nodo selezionato |
| `expression-input.component` | Input riusabile con preview espressioni `$node`/`$item`/`$index` (usa `preview_expression()`) |
| `editor-toolbar.component` | Save, run, attiva/disattiva, zoom, layout |

Il page component resta orchestratore sottile (~250 righe): stato del grafo, wiring dei componenti.

**Perché prima di tutto**: run overlay (3.3), copy/paste/undo (3.4), minimappa (3.5) diventano triviali su un canvas isolato, impraticabili sul monolite.

### 1.2 Shell di navigazione per workflow
Route `workflows/:id` con tab **Editor | Runs | Schedules** scoped sul singolo workflow. Le pagine globali Runs/Schedules restano per la vista cross-workflow. Elimina il giro "lista globale → filtro a mano".

### 1.3 Variabili e credenziali a livello workflow
- `$vars` definite nel workflow (editabili da UI) e `$env` globali di workspace.
- Secrets cifrati (at-rest) referenziabili come `$secrets.<name>` da `http.request`, header, body — mai serializzati nei run log né nell'export.
- Estensione di `expression_resolver.py` + tabella dedicata + sezione nell'inspector.

**Perché in fase 1**: db/file nodes (4.2), export/import (5.2) e template (3.6) presuppongono che le credenziali non vivano hardcoded nel grafo.

### 1.4 Versioning dei workflow
Snapshot immutabile della definizione ad ogni save; ogni run registra `workflow_version_id`. UI: lista versioni, diff (JSON), rollback.

**Perché in fase 1**: da qui in poi ogni feature modifica lo schema del grafo — avere versioni e rollback rende sicuri i cambi successivi; i run diventano riproducibili.

---

## Fase 2 — Affidabilità del motore ✅ COMPLETATA (2026-07-15)

Obiettivo: un run non deve morire per un errore transiente e il sistema deve reggere carico reale.

> **Stato:** tutti e cinque gli item sono implementati e testati (8 nuovi test, 71 verdi sul motore).
> 2.1 — `retry`/`backoff`/`timeoutMs` esistevano già; aggiunti `backoffStrategy` (fixed | exponential, pausa `backoff × 2^tentativo` con tetto 60 s), i campi Backoff/Strategia nell'inspector e i **default dal catalogo** applicati al drop (`http.request`: 2 retry esponenziali 2 s + timeout 60 s; `llm.*`: 1 retry + timeout 120/300 s).
> 2.2 — già implementato in Phase 30: le ondate del DAG girano con `asyncio.gather` sotto il semaforo `GRAPH_WORKFLOW_MAX_CONCURRENT_NODES` (default 8); i `merge` sincronizzano i join.
> 2.3 — colonna `max_concurrent_runs` sul workflow (0 = illimitati, sezione **Esecuzione** nel run panel): i run oltre soglia nascono in stato `queued` (payload parcheggiato in `context_json`) e vengono promossi FIFO da `_maybe_start_queued()` a fine run e allo startup. Cancellabili da coda; i figli `subworkflow` bypassano la coda.
> 2.4 — il checkpoint per ondata ora include gli **handle attivi** di ogni nodo; `resume_interrupted_runs()` allo startup (flag `GRAPH_WORKFLOW_RESUME_ON_STARTUP`) riprende i run `running`/`pending` dal checkpoint rieseguendo solo il sottografo mancante e chiude i node run orfani come "interrupted by restart".
> 2.5 — trigger `error` (+ nodo trigger `error` nel catalogo): scatta al fallimento di un run altrui con `$trigger = {workflow_id, workflow_name, run_id, error, failed_node}`; filtro `config.workflow_id` (vuoto/`*` = tutti), guardie anti-loop (mai self, mai a cascata da run `error`). Esempio curato `error-alert-hub`.
> Doc aggiornate in 5 lingue + examples + frontend-overview + `.env.example`; stato `queued` visibile/filtrabile nella vista Runs.

### 2.1 Retry policy per nodo
Campi `retries`, `backoff` (fixed/exponential), `timeout` nella config di ogni nodo, gestiti in `_run_node()`. UI nell'inspector. Default sensati per `http.request` e `llm.*`. Il branch `onError` scatta solo a retry esauriti.

### 2.2 Esecuzione parallela dei branch
Rami indipendenti del DAG eseguiti con `asyncio.gather` in `_execute()` invece che in sequenza topologica. Limite di parallelismo per run (default es. 5). I nodi `merge` già sincronizzano i punti di join.

### 2.3 Concurrency limit e coda per workflow
`max_concurrent_runs` per workflow; i run oltre soglia vanno in stato `queued` e partono al liberarsi di uno slot. Evita che uno schedule fitto o un webhook a raffica saturi il backend.

### 2.4 Run resumabili / checkpoint
Persistere l'output dei nodi completati durante il run (non solo a fine run). Dopo crash/riavvio, i run `running` riprendono dai nodi non completati. Prerequisito tecnico per `human.approval` (4.4) e `wait` lunghi.

### 2.5 Error trigger
Nuovo trigger `on_workflow_error`: un workflow parte quando un altro (o qualunque, con filtro) fallisce, ricevendo `{workflow_id, run_id, error, failed_node}`. Abilita alerting centralizzato riusando i nodi notify esistenti. Si aggancia a `_maybe_alert_recurring_failures()`/`dispatch_event()`.

---

## Fase 3 — Developer experience nell'editor ✅ COMPLETATA (2026-07-16)

Obiettivo: costruire e debuggare un workflow deve essere veloce quanto in n8n. Dipende dal refactoring 1.1.

> **Stato:** tutti e sei gli item sono implementati e testati (Phase 34; 6 nuovi test backend, 77 verdi sul motore).
> 3.1 — `POST /{id}/nodes/{node_id}/test` + `engine.test_node()`: esegue il singolo nodo con i parametri correnti (anche non salvati, passati nel body come `node`) e input mock opzionale; contesto `$node` da pin/storico, `$trigger` dall'ultimo run; nessun run registrato; risultato `{ok, output, handles, duration_ms}` mostrato inline nell'inspector (⚡ Testa nodo) e proiettato sul canvas.
> 3.2 — campo `pinnedOutput` su `GraphNode` (salvato con il grafo, versionato, esportato): test dei nodi, run parziali e `preview-expression` risolvono `$node.<id>.output` dal pin invece che dallo storico; i run di produzione lo ignorano. UI: sezione 📌 Pin nell'inspector (pin ultimo output / JSON editabile / rimuovi) + badge sul canvas.
> 3.3 — il run overlay esisteva già (Phase 30: nodi colorati per stato via SSE + poll, output live, riaggancio ai run esterni); aggiunta la sezione **Ultima esecuzione** nell'inspector (stato/output/errore del nodo selezionato).
> 3.4 — copy/paste/undo/redo esistevano (Phase 30.c); aggiunte multi-selezione (shift+click, `Ctrl+A`), drag di gruppo, copy/paste della selezione con edge interne (id rimappati), `Canc`/`Backspace`.
> 3.5 — pan (drag su sfondo) + zoom (rotella, ancorato al cursore), minimappa cliccabile/trascinabile con viewport (doppio click = fit), **Riordina** (auto-layout a livelli longest-path, annullabile) e **⛶ adatta vista** in toolbar.
> 3.6 — il pannello esempi è una galleria template: `graph-preview.component` (mini-SVG read-only del grafo) su ogni card + filtro per categoria.
> Doc aggiornate in 5 lingue + frontend-overview + developer-guide.

### 3.1 Esecuzione di test per singolo nodo
"Run this node" dall'inspector: esegue il nodo con gli input correnti (o mock) e mostra l'output inline sul canvas. Endpoint `POST /graph-workflows/{id}/nodes/{node_id}/test`.

### 3.2 Pin/mock dei dati
Congelare l'output di un nodo (es. un payload webhook reale) e riusarlo nelle esecuzioni di test dei nodi a valle mentre si sviluppa il resto del grafo. Pin salvati con il workflow, ignorati nei run di produzione.

### 3.3 Run overlay sul canvas
Replay di un run sul grafo: nodi colorati per stato (success/error/skipped/running), click sul nodo → input/output/durata. La runs page esiste già; manca la proiezione sul canvas. Live via WebSocket/polling per i run in corso.

### 3.4 Copy/paste, duplicazione, undo/redo, multi-selezione
Operazioni standard di editing sul canvas. Undo/redo come stack di comandi sul modello del grafo.

### 3.5 Minimappa e auto-layout
Minimappa per grafi grandi; auto-layout con dagre/elk ("riordina grafo" nella toolbar).

### 3.6 Template gallery
Esporre i workflow di `graph_workflow_examples.py` come galleria "crea da template" nella UI, con anteprima del grafo. Dipende da 1.3 per i template che richiedono credenziali.

---

## Fase 4 — Nuovi nodi e capacità ✅ COMPLETATA (2026-07-16)

Obiettivo: allargare i casi d'uso coperti. Ogni nodo beneficia di retry (2.1) e test per nodo (3.1).

> **Stato:** implementata e testata (Phase 35; 14 nuovi test backend, suite del motore verde).
> 4.1 — nodi `llm.classify` (output `{category, confidence}`, categoria fuori lista ⇒ errore, quindi retry/onError si applicano) e `llm.extract` (JSON Schema nell'inspector, `required` di primo livello verificati, output `{data}`); condividono model picker, failover chain, cache risposte e preset retry di `llm.completion`; il parsing tollera code fence e prosa attorno al JSON.
> 4.2 — `db.query` (sqlite nello storage di workspace; postgres via `dsn` da `$secrets` con asyncpg opzionale; query parametrizzate, output `{rows, count, rowcount}` max 1000 righe) e `file.read`/`file.write`/`file.parse` (formati auto/json/csv/lines, max 10 MB). Sandbox: ogni percorso è risolto dentro `GRAPH_WORKFLOW_FILES_DIR` (default `data/workflow_files`), traversal e percorsi assoluti rifiutati.
> 4.3 — **già implementato** dalle fasi precedenti: `node_catalog()` genera i nodi `tool.*` dinamicamente da `TOOL_DEFINITIONS` (schema parametri incluso) più i tool MCP scoperti e i custom tool del profilo — nessun wrapper manuale residuo.
> 4.4 — nodo `human.approval`: la run va in stato **`waiting`** (nuovo stato, chip viola nella vista Esecuzioni), riga in `workflow_approvals`, notifica in-app (+ Telegram opzionale), attesa con poll fino a decisione o `timeout` (`onTimeout: reject|fail`, tetto `GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT` = 7 giorni); handle di uscita `approved`/`rejected`. API `GET /approvals` + `POST /approvals/{id}/decision`; UI approva/rifiuta nella runs page. Sopravvive ai riavvii (resume 2.4 si riaggancia alla richiesta pendente); cancel chiude la richiesta come `cancelled`; una run `waiting` non occupa slot di `max_concurrent_runs`; il test singolo nodo (3.1) rifiuta il nodo.
> Esempi curati `approval-gate-deploy` e `ticket-triage-classify`; doc aggiornate in 5 lingue + developer-guide + frontend-overview + `.env.example`.

### 4.1 `llm.classify` / `llm.extract`
Output strutturato garantito da JSON schema definito nell'inspector (structured output / tool-use del provider). Molto più robusto di prompt liberi + parsing nel nodo `code`.

### 4.2 Nodi database e file
- `db.query`: SQLite/Postgres, connessione da `$secrets` (1.3), query parametrizzate, output `{rows, count}`.
- `file.read` / `file.write` / `file.parse` (CSV, JSON, testo) su storage di workspace.

### 4.3 Esposizione completa del tool registry
Generare i nodi `tool.*` dinamicamente da `registry.py` in `node_catalog()` (schema parametri incluso) invece dei 4 wrapper manuali attuali. Ogni nuovo tool registrato diventa automaticamente un nodo.

### 4.4 Nodo `human.approval`
Il run si sospende (richiede checkpoint 2.4), invia notifica con link (canali notify esistenti), riprende su approvazione/rifiuto con branch dedicati e timeout configurabile. Sblocca i casi d'uso approvativi aziendali.

---

## Fase 5 — Piattaforma e prodotto ✅ COMPLETATA (2026-07-16)

Obiettivo: da motore interno a prodotto.

> **Stato:** implementata e testata (Phase 36; 8 nuovi test backend, suite del motore verde).
> 5.1 — `GET /v1/graph-workflows/stats`: aggregati per workflow (run per esito, tasso di successo sulle run terminali, durata media, totali token LLM sommati via `json_extract` dalla chiave `_usage` dei nodi `llm.*`). UI: strip di dashboard nella vista Esecuzioni (segue il filtro workflow) + token totali della run nel dettaglio. Nessun costo inventato: solo token (niente listino per modello nel repo).
> 5.2 — l'export include `secrets` (nomi dei `$secrets.<name>` referenziati, mai i valori); `POST /import` dedicato con validazione schema/limite nodi (400) e warning non bloccanti (tipi di nodo sconosciuti, edge rotte, `$secrets` mancanti nel profilo) mostrati come toast; condivisione tra workspace sul pattern Fase 20 (tabella `workspace_workflows`, `GET/POST /{ws}/workflows`, `DELETE /{ws}/workflows/{wid}`, `POST /{ws}/workflows/{wid}/import` = copia "… (shared)" nel profilo del membro con gli stessi warning).
> 5.3 — `POST /generate` ({prompt, model?, failover_chain?}): il catalogo nodi (tipi/output/parametri) fa da contesto all'LLM; la risposta `{name, description, graph}` è validata e **normalizzata** (tipi sconosciuti e edge rotte scartati con warning, trigger `manual` anteposto se manca, auto-layout a livelli per i nodi senza posizione) e torna come **bozza non salvata**; UI: dialogo 🪄 "descrivi cosa vuoi" nell'editor con **model picker + catena di failover** e **log di avanzamento live** via `POST /generate/stream` (eventi SSE `log` per fase — catalogo, chiamata, risposta, validazione, layout — poi `done`/`error`), la bozza si apre per revisione.
> Extra UX (stessa data): galleria template come modale grande centrata (card più dettagliate: anteprima maggiore, categoria, catena del flusso, conteggi) ed elenco workflow comprimibile (preferenza persistita) a favore della palette.
> Doc aggiornate in 5 lingue + developer-guide + frontend-overview; i18n 5 lingue.

### 5.1 Metriche e osservabilità
Durata per nodo, token/costo LLM per run (aggancio al provider layer), success rate per workflow. Dashboard aggregata + colonna costi nella runs page.

### 5.2 Export/import e condivisione
Export JSON del workflow (senza secrets, con placeholder da rimappare all'import), import con validazione schema, condivisione tra workspace. Dipende da 1.3 e 1.4.

### 5.3 Workflow generati da LLM
"Descrivi cosa vuoi" → il backend genera il JSON del grafo usando lo schema dei nodi come contesto, lo valida e lo apre in editor come bozza. Riusa provider layer + `node_catalog()` (arricchito da 4.3). Feature di punta lato prodotto.

---

## Riepilogo ordine e razionale

| Fase | Tema | Item chiave | Sblocca |
|---|---|---|---|
| 1 | Fondamenta | Refactor editor, shell navigazione, vars/secrets, versioning | Tutto il resto a costo ridotto |
| 2 | Affidabilità | Retry, parallelismo, coda, checkpoint, error trigger | Produzione reale; human.approval |
| 3 | DX editor | Test nodo, pin data, run overlay, undo/redo, template | Sviluppo workflow 10× più rapido |
| 4 | Nuovi nodi | classify/extract, db/file, tool registry, human.approval | Nuovi casi d'uso |
| 5 | Prodotto | Metriche/costi, export/import, generazione da LLM | Valore commerciale |

Primo sprint consigliato: **1.1 + 1.2** (refactoring UI) in parallelo a **2.1** (retry per nodo, solo backend) — nessuna dipendenza incrociata e valore immediato su entrambi i fronti.
