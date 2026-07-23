# Workflow visuali a grafo di nodi (Fase 29)

SpiceSibyl ha due motori di automazione complementari:

- **Workflow ad agente** (`/workflows`, Fase 18) — dai un *obiettivo* e un LLM itera
  autonomamente su tutto il registro di tool finché non produce una risposta. Potente ma
  non deterministico e senza flusso di controllo esplicito.
- **Workflow visuali** (`/graph-workflows`, Fase 29) — disegni un *grafo*: un **trigger**
  alimenta **nodi tipizzati** collegati tra loro. Il motore esegue il grafo in modo
  **deterministico**, nella forma esatta che hai progettato. Il loop ad agente resta
  disponibile qui come nodo `llm.agent`, così puoi inserire autonomia dove serve dentro
  una pipeline deterministica.

![Editor dei workflow visuali](screenshots/visual-workflow-editor.svg)

> **Di fretta?** Clicca ✨ nella pagina `/graph-workflows` e premi **Importa** su uno dei
> venticinque [grafi di esempio](../examples/graph-workflows.md) già pronti — uno per
> funzionalità (logica, loop, dati, file, DB, notifiche, AI, chat, trigger) — si apre sul
> canvas pronto da modificare ed eseguire.


![Visual editor — componentized canvas, palette and run panel](../screenshots/editor-overview.png)

<p align="center">
  <img src="../screenshots/run-panel-vars-secrets-versions.png" alt="Run panel: $vars editor, $secrets manager, version history" width="360" />
</p>

![Per-workflow shell — Editor | Runs | Schedules tabs with the run detail open](../screenshots/workflow-shell-runs.png)

## Il canvas

L'editor ha tre pannelli:

- **Sinistra** — i tuoi workflow e una **palette di nodi** categorizzata (Trigger · Azioni
  · Logica · Dati · IA). Ogni tool built-in, MCP e custom appare automaticamente come nodo
  `tool.<nome>`, senza scrivere codice per ogni tool.
- Una barra strumenti sopra il canvas offre **Annulla/Ripeti** (`Ctrl+Z` / `Ctrl+Shift+Z`,
  anche `Ctrl+Y` per ripetere), **Copia/Incolla** un nodo (`Ctrl+C` / `Ctrl+V` — incolla un
  duplicato con offset, stesso tipo e parametri) e **Commento**: un nodo "sticky note"
  solo lato client per annotare il canvas, senza handle di input/output e mai collegato al
  flusso — il motore lo registra semplicemente come `skipped`, nessuna modifica al backend.
  Le scorciatoie sono ignorate mentre si scrive in un campo. Un **campo di ricerca** sopra
  la palette filtra i nodi per etichetta o tipo (espandendo automaticamente i gruppi
  MCP/custom con corrispondenze durante la ricerca).
- **Centro** — un **canvas SVG** senza dipendenze. Trascina i nodi per posizionarli;
  trascina da un **handle di output** (a destra) all'**handle di input** (a sinistra) di un
  altro nodo per collegarli. **Clicca su un collegamento** per ispezionarlo: il pannello di
  destra mostra sorgente → destinazione, i **dati transitati nell'ultima esecuzione** e
  l'elenco appiattito dei **campi disponibili con il percorso espressione già pronto**
  (es. `$node.weather.output.result`) — un clic sul campo lo copia come espressione
  `{{ … }}`. Un pulsante elimina il collegamento.
  **Auto-mapping alla connessione**: appena disegni un collegamento, l'editor precompila
  il primo parametro vuoto (di tipo espressione) del nodo di destinazione con l'output del
  nodo sorgente. La forma dell'output viene dedotta dall'ultimo dato registrato (live o
  dallo storico esecuzioni) — testo, numero, lista (con lunghezza), oggetto (con le sue
  chiavi). Se il valore è uno solo e c'è un solo campo vuoto, il mapping è applicato in
  automatico (un toast lo conferma). Altrimenti si apre un **dialog di scelta**: elenca
  ogni valore candidato con il percorso espressione, il **tipo** e un'**anteprima** per
  capirne la differenza, permette di scegliere quale campo compilare se ce n'è più di uno
  vuoto, e offre *Non ora* per saltare. I campi già compilati dall'utente non vengono mai
  sovrascritti. Il mapping è **consapevole dei loop**: collegando dall'uscita `loop` di un
  nodo for/repeat propone `$item` / `$index` (lo scope per-iterazione — `$node.<loopId>.output`
  non esiste dentro il corpo), mentre dall'uscita `done` propone `…output.items`; e un
  parametro `items` di destinazione (for/filter/aggregate/batch) preseleziona il primo
  valore di tipo lista, es. il `.json` parsato di un nodo tool invece del testo `.result`. Quando un nodo fallisce, il suo **messaggio
  di errore** appare in rosso sotto il nodo nel pannello live (e nel dettaglio della
  vista Esecuzioni).
- **Destra** — l'**ispettore** del nodo selezionato (i suoi parametri, generati dallo schema
  del tipo di nodo) oppure, quando non è selezionato nulla, il **pannello esecuzione e trigger**.

Salva con **Salva**, attiva **Attivo** per far scattare i trigger e **Esegui ora** per
lanciare subito il grafo — i nodi si colorano di verde/blu/rosso/grigio (ok/in esecuzione/
errore/saltato) in tempo reale mentre il motore trasmette lo stato via SSE. Il pannello di
esecuzione ha un campo opzionale **Payload di esecuzione** (JSON): l'oggetto diventa
`$trigger` della run, così i grafi che leggono `={{ $trigger.<campo> }}` (come gli esempi
webhook e subworkflow) si possono provare a mano senza una chiamata webhook.

### La vista per singolo workflow — `/graph-workflows/{id}`

Ogni workflow ha anche una pagina dedicata (aprila con il pulsante ⧉ nella lista, o da
una riga di esecuzione/pianificazione): una barra a tab **Editor | Esecuzioni |
Pianificazioni** limitata a quel workflow. Il tab Esecuzioni è il registro filtrato sul
workflow; il tab Pianificazioni elenca e crea trigger solo per esso. Le pagine globali
(`/graph-workflows`, `/graph-workflows/runs`, `/graph-workflows/schedules`) restano le
viste trasversali.

L'editor stesso è componentizzato (roadmap fase 1): canvas SVG, palette, toolbar,
inspector nodo/arco e run panel sono componenti Angular standalone in
`features/workflows/editor/`, orchestrati da un page component sottile — vedi
`docs/frontend-overview.md`.

### DX dell'editor — test, pin, navigazione (fase 3)

Costruire e debuggare un grafo non richiede run completi:

- **Testa nodo** (⚡ nell'inspector) esegue **solo il nodo selezionato**, con i parametri
  correnti — anche non salvati — e mostra output, handle attivo e durata inline
  (`POST /{id}/nodes/{node_id}/test`; nulla viene registrato nel registro esecuzioni).
  L'input arriva dall'output pinnato/più recente del nodo a monte, oppure dal JSON di
  **input mock** opzionale nell'inspector.
- **Pin degli output** (📌): congela l'output di un nodo — un click sull'ultimo output,
  o JSON modificato a mano. Test dei nodi, **run parziali** (*Esegui da questo nodo*) e
  anteprime delle espressioni risolvono `$node.<id>.output` dal pin invece che dallo
  storico: ideale per sviluppare a valle di un payload webhook reale senza rilanciarlo.
  I pin sono salvati con il workflow (e viaggiano con l'export), mostrano un badge 📌
  sul canvas e sono **completamente ignorati dai run di produzione**
  (manual/schedule/webhook/event).
- **Ultima esecuzione** nell'inspector mostra stato, output ed errore più recenti del
  nodo selezionato (run live, test o storico) senza lasciare il canvas.
- **Multi-selezione**: shift+click aggiunge/rimuove nodi; il drag sposta l'intera
  selezione; `Ctrl+A` seleziona tutto; `Ctrl+C/V` copia e incolla la selezione
  **incluse le edge interne** (id rimappati); `Canc`/`Backspace` la elimina.
- **Pan & zoom**: trascina il canvas vuoto per fare pan, rotella per zoomare attorno al
  cursore. Una **minimappa** (in basso a destra) mostra l'intero grafo più il viewport —
  click/drag per navigare, doppio click per adattare. La toolbar aggiunge **Riordina**
  (auto-layout a livelli, annullabile come ogni modifica) e **⛶ adatta vista**.
- La **galleria di template** (✨) si apre come **modale grande centrata** sopra
  l'editor: griglia multi-colonna di card, ognuna con anteprima del grafo più grande,
  categoria, catena del flusso (nomi dei nodi uniti da →), conteggio nodi/connessioni e
  descrizione completa — filtrabile per categoria prima dell'import. L'**elenco dei
  workflow è comprimibile** (▾/▸ nell'intestazione, la preferenza è ricordata tra le
  sessioni), così la palette dei nodi guadagna lo spazio della sidebar mentre si edita.

## Tipi di nodo

| Categoria | Nodi |
|-----------|------|
| **Trigger** | `manual`, `schedule`, `webhook`, `event`, `error`, `success` (completamento di un altro workflow — fase 6.1), `file.watch` / `email.inbound` (trigger a polling verso il mondo esterno — fase 6.2) |
| **Azione** | `tool.<nome>` — qualsiasi tool **integrato** (RSS, read_url, meteo, kb_search, http_request, python_exec…) · `http.request` (chiamata HTTP generica) · `subworkflow` (esegue un altro workflow inline) · `human.approval` (sospende finché un umano approva/rifiuta — fase 4.4) · `human.input` (sospende finché un umano compila un form JSON-Schema — fase 10.1) · `wait.event` (sospende finché arriva un evento esterno correlato — fase 10.2) |
| **MCP e custom** | ogni **tool MCP scoperto** (`tool.mcp__<server>__<tool>`) e i **tool HTTP custom** del profilo (`tool.custom__<nome>`) compaiono come nodi trascinabili — nessun codice per tool |
| **Logica** | `if` (ramo vero/falso), `switch` (rami per caso), `merge` (raccoglie gli input), `for` (for-each su un array), `repeat` (N volte), `wait` (attende N secondi o fino a un istante preciso) |
| **Dati** | `set` (costruisce un oggetto), `filter` (tiene gli elementi che soddisfano la condizione), `code` (sandbox Python), `aggregate` (riduce un array — sum/avg/min/max/count/concat su un campo), `batch` (spezza un array in blocchi di dimensione fissa), `db.query` (SQL parametrizzato — sqlite/postgres), `file.read` / `file.write` (storage di workspace), `file.parse` (parsa JSON/CSV/righe in transito) |
| **Notifiche** | `notify.telegram` (chat Telegram collegata), `notify.email` (SMTP), `notify.webhook` (Slack/Discord/ntfy/webhook qualsiasi), `notify.inapp` (campanella della web UI, zero configurazione) |
| **IA** | `llm.completion` (una chiamata al provider), `llm.agent` (l'intero loop ad agente della Fase 18, con accesso a tool integrati + MCP + custom), `llm.classify` / `llm.extract` (output strutturato garantito — fase 4.1) |

> **MCP nei flussi** — la palette è scoperta per profilo: qualsiasi server MCP configurato su
> `/mcp` e qualsiasi tool custom da `/tools` appare nel gruppo **MCP e custom** e viene eseguito
> nativamente (l'executor `tool.<nome>` instrada i nomi `mcp__*` / `custom__*`). Anche il nodo
> `llm.agent` riceve l'intero set di tool, quindi un nodo autonomo può usare MCP e tool custom.

> **Selezione del modello** — `llm.completion` e `llm.agent` mostrano un **selettore di modello
> con lo stesso catalogo e gli stessi filtri della pagina chat** (filtri provider / capacità /
> solo gratuiti, ricerca per nome e i modelli nascosti su `/providers`), così scegli il modello
> qui esattamente come nella chat. Si espande in linea nell'inspector (non un popup fluttuante).

> **Catene di failover** — entrambi i nodi mostrano anche un menu **Failover chain**,
> popolato dagli elenchi di modelli nominati curati in Impostazioni → Modelli → Catene di
> failover LLM (modificabile solo dagli admin, visibile a tutti nel selettore). Se impostata,
> un fallimento della chiamata sul `model` del nodo riprova — in ordine — attraverso i
> modelli restanti della catena finché uno non ha successo o si esauriscono; l'output del
> nodo include allora `_failover: { tried: [...], used: "<model>" }`. Per `llm.agent`, un
> fallback riuscito è persistente: i passi successivi del loop partono dal modello appena
> funzionante, invece di riprovare sempre quello originale.

### Richieste HTTP — `http.request`

Un nodo di prima classe per chiamare **qualsiasi API HTTP esterna** (senza definire un
tool). Parametri: `method`, `url`, `query` / `headers` (oggetti JSON), `body` (un valore
JSON viene inviato come JSON, il resto come testo grezzo), `timeout` (secondi, max 120).
L'output è `{ status, ok, headers, json, text }` — `json` è il corpo già parsato quando la
risposta è JSON, così a valle puoi leggere `={{ $node.api.output.json.<campo> }}`.

Di default una risposta **non-2xx solleva un errore**, quindi si applicano retry e la
politica *In caso di errore* (vedi sotto) — ideale per pattern "riprova due volte, poi
avvisa". Imposta `allow_errors` a un valore veritiero per ricevere comunque la risposta.

### Composizione — `subworkflow`

Esegue **un altro workflow dello stesso profilo inline** come run figlia e ritorna quando
questa termina. Parametri: `workflow_id` e un `payload` opzionale (oggetto JSON) che
diventa il `$trigger` del figlio; senza payload viene passato l'input di questo nodo come
`{ input: … }`. L'output è `{ run_id, workflow_id, status, output }`, dove `output` è
l'**output del nodo terminale** del figlio (o una mappa se i terminali sono più di uno).
Il figlio è una run normale e osservabile (`trigger_type: subworkflow`) con i propri
record per nodo e stream SSE. L'annidamento è limitato a **5 livelli** e l'auto-ricorsione
fa fallire la run invece di ciclare all'infinito.

### IA strutturata — `llm.classify` / `llm.extract` (fase 4.1)

Due nodi IA con **forma dell'output garantita**, che sostituiscono il fragile pattern
"prompt libero + parsing JSON in un nodo `code`":

- **`llm.classify`** — classifica `input` (espressione; default: l'input del nodo) in una
  delle `categories` dichiarate (array JSON o lista separata da virgole). Il modello deve
  rispondere `{category, confidence}` con una categoria **della lista** — qualsiasi altra
  risposta solleva errore, quindi si applicano retry / *In caso di errore*. Output:
  `{ category, confidence, model, _usage }`. Instrada il risultato con uno `switch` su
  `={{ $node.<id>.output.category }}`.
- **`llm.extract`** — estrae dati strutturati conformi a un **JSON Schema** dichiarato
  nell'inspector (parametro `schema`). Le proprietà `required` di primo livello sono
  verificate; una risposta non conforme solleva errore. Output: `{ data, model, _usage }`.

Entrambi espongono lo stesso selettore modelli e la **catena di failover** di
`llm.completion`, usano la cache delle risposte e arrivano con preset di retry
(1 retry esponenziale, timeout 120 s). Code fence e testo attorno al JSON sono tollerati.

### Database e file — `db.query`, `file.read`, `file.write`, `file.parse` (fase 4.2)

- **`db.query`** — esegue SQL parametrizzato e produce `{ rows, count, rowcount }` (max
  1000 righe). `driver: sqlite` (default) tiene il file del database **dentro lo storage
  di workspace** (`database` è un percorso relativo, es. `app.db`); `driver: postgres` si
  connette via `dsn` — tienilo in `$secrets` (`={{ $secrets.PG_DSN }}`), mai inline. Usa i
  placeholder `?` (sqlite) / `$1…` (postgres) con l'array JSON `params`.
- **`file.read`** — legge un file dallo storage di workspace e lo parsa per `format`
  (`auto` dall'estensione): `json → {data}`, `csv → {rows, count}`, `lines → {lines,
  count}`, `text → {text, size}`. Limite 10 MB.
- **`file.write`** — scrive `content` (o l'input del nodo); oggetti/array serializzati
  come JSON, `format: csv` rende una lista di oggetti con intestazione, `append: true`
  accoda. Output: `{ path, format, bytes_written, append }`.
- **`file.parse`** — parsa un **payload testuale in transito** (body di `http.request`,
  risultato di un tool…) senza toccare il disco, stessi output di `file.read`.

**Sandbox** — ogni percorso è risolto *dentro* `GRAPH_WORKFLOW_FILES_DIR` (default
`data/workflow_files`); percorsi assoluti e traversal `..` che ne uscirebbero fanno
fallire il nodo. Le credenziali di database esterni vanno in `$secrets` (fase 1.3).

### Human-in-the-loop — `human.approval` (fase 4.4)

La run si **sospende** su questo nodo (stato `waiting`, chip viola) finché un umano non
decide. All'esecuzione crea una richiesta di approvazione, invia una **notifica in-app**
(opzionale Telegram con `telegram: true`) e attende. Si decide dalla vista **Esecuzioni** —
aprendo una run `waiting` compare la richiesta con **✓ Approva / ✕ Rifiuta** e commento
opzionale — o via API. La decisione instrada il grafo sull'handle **`approved`** o
**`rejected`** con `{ approved, status, comment, decided_by }` come output.

Parametri: `title`, `message` (espressione), `timeout` (secondi, default 24 h, tetto
`GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT` — default 7 giorni) e `onTimeout` (`reject` instrada
la richiesta scaduta sul ramo rejected; `fail` fa fallire il nodo). Grazie ai checkpoint
della fase 2.4 l'attesa **sopravvive ai riavvii**: una run ripresa si riaggancia alla
richiesta pendente invece di crearne una nuova. Annullare una run `waiting` chiude la
richiesta come `cancelled`. Una run `waiting` **non** occupa uno slot di
`max_concurrent_runs`.

```
GET  /v1/graph-workflows/approvals                 ?status=pending&run_id=   (lista)
POST /v1/graph-workflows/approvals/{aid}/decision  { approved: true|false, comment? }
```

### Human-in-the-loop avanzato — `human.input`, `wait.event` (fase 10)

Altri due nodi sospendono la run (`waiting`) allo stesso modo di `human.approval`,
generalizzando la sua riga di richiesta in un `kind` (`approval` | `input` | `event`) così
che tutti e tre condividano lo stesso ciclo di poll/ripresa e sopravvivano a un riavvio del
backend allo stesso modo.

**`human.input`** — la richiesta porta con sé un **form definito da JSON Schema**
(parametro `schema`: campi, tipi, `required`, `enum`). Si decide dalla vista Esecuzioni (i
campi vengono renderizzati come un form) o via API; il `data` inviato viene **validato
rispetto allo schema** prima di essere accettato. La run riprende sul ramo **`submitted`**
con `{ data, status, comment, decided_by }` come output; uno scadere segue `onTimeout`
(`branch` instrada sul ramo **`timeout`**, `fail` fa fallire il nodo). Sblocca i flussi
"chiedi all'operatore il valore mancante" — ad esempio un importo di spesa e la categoria
prima di continuare.

```
POST /v1/graph-workflows/approvals/{aid}/submit  { data: {...}, comment? }
```

**`wait.event`** — la run si sospende finché un **sistema esterno** consegna un evento con
un **correlation id** corrispondente. `correlationId` (espressione, es. un order id da
`$trigger`) definisce la chiave; `POST /v1/graph-workflows/events/{correlation_id}`
(autenticato, con scope sul profilo) risveglia la run e consegna il suo `payload` come
**output** del nodo, attraverso il ramo **`main`**. Stessi `timeout` / `onTimeout`
(`branch` | `fail`) di `human.input`. Copre i veri callback asincroni — pagamenti, firme
digitali, ticket, webhook di terze parti — senza polling. Una run `waiting` non occupa uno
slot di `max_concurrent_runs`.

```
POST /v1/graph-workflows/events/{correlation_id}  { payload: {...} }
```

Parametri (entrambi i nodi): `title`, `message` (espressione), `timeout` (secondi, default
24 h, tetto `GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT`), `onTimeout`. `human.input` prende
inoltre `schema` (il JSON Schema del form); `wait.event` prende `correlationId` al suo
posto.

### Notifiche — `notify.*`

Quattro nodi terminali consegnano il risultato di un workflow su un canale; combinali
con il ramo di errore per flussi "avvisami quando si rompe":

- **`notify.telegram`** — invia `text` alla **chat Telegram collegata al profilo**
  (Impostazioni → Telegram, lo stesso ponte delle notifiche dei promemoria). Fallisce se
  nessuna chat è collegata; una chat silenziata (`/notify off`) è un no-op silenzioso. Un
  `parse_mode` opzionale (`Markdown` / `MarkdownV2` / `HTML`, vuoto = testo semplice) fa
  renderizzare la formattazione invece di mostrare il markup grezzo — utile quando `text`
  arriva da un nodo `llm.*` che scrive CommonMark. Il `**grassetto**` (CommonMark) viene
  normalizzato automaticamente nel `*grassetto*` a singolo asterisco di Telegram quando
  scegli una modalità Markdown, perché Telegram non riconosce il doppio asterisco e lo
  stamperebbe altrimenti alla lettera. I messaggi oltre il limite di 4096 caratteri di
  Telegram vengono divisi automaticamente in più messaggi lungo i confini di riga, così i
  digest lunghi non vengono mai persi.
- **`notify.email`** — email in testo semplice (`to`, `subject`, `body`) tramite il
  server SMTP configurato con `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD`
  / `SMTP_FROM` / `SMTP_STARTTLS`. Senza SMTP configurato il nodo fallisce, quindi si
  applicano retry e la politica di errore.
- **`notify.webhook`** — POSTa un `payload` JSON (di default l'input del nodo) a un
  webhook esterno qualsiasi — webhook in ingresso Slack/Discord, ntfy, domotica, …
- **`notify.inapp`** — spinge `title`/`body` alla **campanella di notifica della web
  UI** (persistita e trasmessa live via SSE). Zero configurazione — il default sicuro.

### La vista Esecuzioni — registro delle run

Il designer tiene solo un pannello live leggero; il registro durevole vive nella pagina
**Esecuzioni** (`/graph-workflows/runs` — voce di menu a sé sotto Strumenti, controllata
dallo stesso flag `graph_workflows` del designer nei Settings, oltre al link
"Esecuzioni →" nell'header dell'editor). Elenca tutte le run del profilo su tutti i
workflow — stato, trigger, avvio, durata — filtrabili per workflow e stato, con
auto-aggiornamento finché qualcosa è in esecuzione. Dalla stessa barra puoi **avviare un
workflow** (scegli, incolla opzionalmente un payload JSON `$trigger`, premi Avvia) e
**fermare** qualsiasi run in corso (`POST /v1/graph-workflows/runs/{id}/cancel` — il
motore cancella il task e la run si assesta su `cancelled`).
Selezionando una run vedi i risultati per nodo (stato, errore, output) e, se è ancora in
corso, la segui **live via SSE**; "Apri nel designer" torna al grafo. Cambiare workflow
nel designer non fa più perdere un'esecuzione: l'editor si riaggancia all'ultima run in
corso quando riapri il suo workflow, e la vista Esecuzioni è sempre la fonte di verità
(`GET /v1/graph-workflows/runs`).

### Gestione errori — retry e ramo di errore

Ogni nodo ha tre controlli di errore nella sezione **Avanzate** dell'ispettore:

- **Tentativi** / backoff — riesegue il nodo fino a N volte, attendendo `backoff` secondi
  tra un tentativo e l'altro. La **Strategia di backoff** (fase 2.1) decide come cresce la
  pausa: **Fisso** attende sempre `backoff` secondi; **Esponenziale** attende
  `backoff × 2^tentativo` (1º retry dopo `backoff`s, 2º dopo `2×backoff`s, …), con tetto
  di 60 s per pausa. I nuovi nodi `http.request` e `llm.*` arrivano già preconfigurati con
  preset sensati dal catalogo della palette (es. HTTP: 2 tentativi, backoff esponenziale
  2 s, timeout 60 s) — regolabili o azzerabili per nodo.
- **Timeout (ms)** — limite rigido di tempo per un *singolo* tentativo di esecuzione (`0`
  lo disabilita, max 600 000). Un tentativo scaduto viene interrotto e fallisce come
  qualsiasi altro errore, quindi resta soggetto a tentativi/backoff e alla politica **In
  caso di errore** qui sotto — la protezione idiomatica per un `http.request`, un
  `llm.agent` o un tool MCP bloccato che altrimenti impallerebbe l'intera run.
- **In caso di errore** — cosa succede esauriti i tentativi:
  - **Interrompi la run** (default) — la run fallisce.
  - **Continua sul ramo principale** — il nodo emette `{ error }` sull'uscita `main` e il
    flusso continua (il vecchio flag `continueOnFail` si comporta allo stesso modo).
  - **Instrada sul ramo di errore** — il nodo espone un **handle di uscita `error`**
    dedicato; in caso di fallimento `{ error, input }` scorre su quel ramo mentre il ramo
    `main` viene saltato (e viceversa in caso di successo). È un try/catch disegnato sul
    canvas: collega il percorso felice a `main` e la catena di fallback/allerta a `error`.

Il nodo viene comunque registrato (e colorato) come **errore** quando instrada sul ramo di
errore, così lo storico resta veritiero mentre la run si completa.

### Cicli — `for` e `repeat`

`for` e `repeat` hanno due uscite: **`loop`** (il corpo) e **`done`** (la continuazione).
Collega la catena del corpo all'uscita `loop` e il resto del flusso a `done`:

- **`for`** prende un array (`items`, es. `={{ $trigger.urls }}`) ed esegue il corpo **una volta
  per elemento**, con `$item` e `$index` disponibili in quell'iterazione. Anche i nodi del corpo
  già eseguiti **nella stessa iterazione** sono leggibili come `$node.<id>.output` (quindi i
  percorsi dell'ispettore dei collegamenti funzionano anche dentro il corpo); ogni iterazione
  vede solo i propri valori.
- **`repeat`** esegue il corpo un numero fisso di `times`, con `$index` disponibile.

Il risultato del corpo di ogni iterazione viene raccolto; al termine il ciclo produce
`{ items: [...], count }` su `done`, così la continuazione può leggere
`={{ $node.<idLoop>.output.items }}`. Il corpo è il sottografo raggiungibile da `loop`
(e non da `done`); tienilo come catena lineare. Le iterazioni sono limitate per sicurezza.

## Espressioni

Ogni parametro può essere un valore letterale **o** un'espressione. Due forme, distinte dal prefisso:

- `={{ … }}` — una **mini-espressione sicura**. Viene analizzata e valutata su una whitelist
  (**niente `eval`/`exec`**), quindi è sicura nell'interfaccia. Puoi navigare il contesto di
  esecuzione e chiamare un set fisso di funzioni pure:

  ```
  ={{ $node.rss.output.result }}          # output di un altro nodo
  ={{ $trigger.count }}                    # payload del trigger
  ={{ upper($json.title) }}                # funzione whitelisted
  ={{ default($trigger.name, 'mondo') }}
  ={{ $trigger.count > 3 }}                # confronti → if/switch
  Ciao ={{ $trigger.name }}!               # interpolazione di stringa
  ```

  Contesto: `$node.<id>.output.<path>`, `$json` (input primario del nodo), `$trigger`,
  `$env` (variabili d'ambiente con prefisso WF_), `$vars` (variabili del workflow), `$secrets` (secrets del profilo, decifrati solo durante la run), `$now`. Funzioni: `default`, `upper`,
  `lower`, `trim`, `len`, `join`, `slice`, `first`, `last`, `get`, `keys`, `values`, `round`, …

- `=py: …` — una **via di fuga** verso la sandbox `python_exec` per logica reale
  (comprehension di liste, ecc.). Sono disponibili `ctx`, `input`, `node`, `trigger`;
  l'ultima espressione (o una variabile `result`) diventa il valore.

Tutto ciò che non inizia con `=` è un letterale — con un'eccezione tollerante: un
`{{ … }}` nudo (senza il `=` iniziale) è un errore così comune che viene risolto
esattamente come `={{ … }}`.

Un'espressione **da sola** mantiene il suo tipo nativo (lista, dict, numero…); appena la
circondi di testo il risultato diventa una stringa interpolata. Spazi e a-capo attorno
all'espressione non contano: `{{ … }}` seguito da un invio accidentale nella textarea
resta nativo — importante per il parametro `items` di For-each/Filter, che vuole una
lista vera.

> **I nodi non collegati non partono** — solo i nodi *trigger* sono punti di ingresso.
> Un nodo trascinato sul canvas ma non collegato al flusso viene registrato come
> `skipped` all'esecuzione invece di partire da solo.

## Variabili & secrets — `$vars` / `$secrets`

Due ambiti di configurazione tolgono i valori dai parametri dei nodi (roadmap fase 1):

- **Variabili (`$vars`)** — coppie chiave/valore per workflow, modificabili nella sezione
  *Variabili* del run panel e leggibili da ogni nodo come `{{ $vars.nome }}`. Un valore
  che è JSON valido mantiene il tipo nativo (lista, oggetto, numero, booleano). Le
  variabili viaggiano con Export/Import e con l'API (`variables` su `POST`/`PATCH`);
  cambiarle **non** incrementa la versione del grafo.
- **Secrets (`$secrets`)** — credenziali a livello di profilo condivise da tutti i tuoi
  workflow (token API, stringhe di connessione…), gestite nella sezione *Secrets* del run
  panel. I valori sono **cifrati a riposo con Fernet** (chiave derivata da
  `VAULT_SECRET_KEY`, lo stesso master secret del vault delle API key) e **mai restituiti
  dall'API** — la lista mostra solo i nomi. Si referenziano come `{{ $secrets.NOME }}`
  (es. in un header di `http.request`). Il motore li decifra solo per la durata della
  run; il contesto persistito non li contiene mai, il *Test expression* dell'editor li
  risolve come `***` e l'Export li omette di proposito — vanno ricreati nell'ambiente di
  destinazione.

## Trigger

Dal pannello di esecuzione puoi collegare:

- **Schedule** — cron / RRULE / linguaggio naturale ("ogni giorno alle 9:00"), interpretato
  dallo stesso motore dei promemoria. Un loop di polling in background esegue gli schedule
  scaduti e ricalcola il prossimo orario. (Scatta solo quando il workflow è **Attivo**.)
- **Webhook** — un URL pubblico con token (`POST /api/v1/wf/hooks/{token}`). Il corpo JSON
  diventa `$trigger`. Scatta solo quando il workflow è Attivo. Puoi proteggerlo con un
  segreto condiviso: `POST /v1/graph-workflows/triggers/{tid}/rotate-secret` ne genera uno
  (mostrato una sola volta) e da quel momento la richiesta deve avere l'header
  `X-Signature: sha256=<hmac-sha256 esadecimale del corpo grezzo>`, altrimenti viene
  rifiutata con 401 prima ancora di essere interpretata.
- **Event** — eventi interni. Imposta `config.event` sul nome dell'evento (vuoto o `*` per
  intercettarli tutti). Oggi sono cablati due eventi: `document.ingested` (dopo l'ingest di
  un documento/URL nella KB — payload `{doc_id, filename, profile_id}`) e
  `chat.message.created` (dopo che uno scambio di chat viene salvato — payload
  `{conversation_id, profile_id}`).
- **Error** (fase 2.5) — scatta quando la run di *un altro* workflow fallisce.
  `config.workflow_id` lo restringe a un workflow osservato (vuoto / `*` = tutti). Il
  payload è `{workflow_id, workflow_name, run_id, error, failed_node}`; sul canvas usa il
  *nodo* trigger `error` come punto d'ingresso. Protetto dai loop: un workflow non
  reagisce mai ai propri fallimenti e le run partite da un trigger error non innescano
  altri trigger error a cascata. Ideale per l'alerting centralizzato con i nodi `notify.*`.

Sia i trigger **schedule** che **event** tengono un contatore di fallimenti consecutivi
(`fail_count`/`last_error`): dopo `GRAPH_WORKFLOW_TRIGGER_MAX_FAILURES` (default 5)
fallimenti di fila il trigger si disabilita da solo e viene inviata una notifica in-app,
così un trigger rotto non fallisce in silenzio per sempre. Riabilitarlo
(`POST /triggers/{tid}/enable`) azzera il contatore.

### Vista Schedulazioni — panoramica trigger multi-workflow

`/graph-workflows/schedules` (Fase 30.e, stesso gruppo di navbar e feature flag) elenca
**una riga per trigger** su tutti i workflow del profilo: nome workflow, tipo di trigger,
prossima esecuzione (trigger schedule), stato/orario dell'ultima esecuzione, contatore di
fallimenti consecutivi e un interruttore abilita/disabilita — così vedi tutto ciò che è in
scadenza, o rotto, senza aprire ogni workflow singolarmente, oltre a **Esegui** ed
**Elimina**. Backend: `GET /v1/graph-workflows/schedules`.

> **Un trigger scatta solo se il suo *workflow* è Attivo** — l'abilitazione del trigger è
> indipendente dal flag Attivo del workflow (si cambia dal designer, oppure con la
> pillola Attivo/Inattivo accanto al nome del workflow qui). Un trigger perfettamente
> configurato e abilitato su un workflow Inattivo non scatterà mai; il pannello
> **+ Nuovo trigger** avvisa e offre un'attivazione con un click quando il workflow scelto
> è Inattivo, perché è la causa più comune di una schedulazione appena creata che non
> fa nulla in silenzio.

**Creare un trigger** (Fase 30.f) — il pannello **+ Nuovo trigger** sceglie un workflow e
un tipo (`schedule`/`webhook`/`event`); per `schedule` espone un pattern strutturato invece
del linguaggio naturale libero: **Giornaliero** (un orario HH:MM), **Settimanale** (uno o
più giorni + orario), **Cron** (preimpostazioni come "ogni 15 minuti"/"ogni ora"/"ogni
giorno a mezzanotte"/"feriali alle 9:00" che riempiono un **campo cron libero a 5 campi**,
sempre modificabile, validato con `croniter`), **Una tantum** (data opzionale + orario). I
trigger `event` prendono un nome evento libero (`document.ingested` e
`chat.message.created` sono cablati oggi); i `webhook` non richiedono config qui — il
segreto di firma si genera/ruota dal designer dopo la creazione.

### Produzione: concorrenza, utilizzo token, alert

- **Limite di concorrenza** — un semaforo `GRAPH_WORKFLOW_MAX_CONCURRENT_NODES` (default 8)
  limita quanti nodi indipendenti girano in parallelo all'interno di una stessa esecuzione.
- **Coda di run per workflow** (fase 2.3) — imposta **Run concorrenti max** nella sezione
  **Esecuzione** del pannello run (o `max_concurrent_runs` via API, `0` = illimitati): le
  run oltre il limite nascono in stato **`queued`** (con il payload del trigger parcheggiato
  nella run) e partono in ordine FIFO quando si libera uno slot. Uno schedule fitto o una
  raffica di webhook non saturano più il backend. Le run in coda compaiono nella vista Runs
  e si possono annullare come le altre. Le run figlie dei `subworkflow` bypassano la coda
  (una figlia in coda bloccherebbe il genitore in attesa).
- **Checkpoint e ripresa** (fase 2.4) — il contesto della run (l'output di ogni nodo **e i
  suoi handle di uscita attivi**) viene persistito dopo ogni ondata. Allo startup (gated da
  `GRAPH_WORKFLOW_RESUME_ON_STARTUP`, default true) le run rimaste `running`/`pending` per
  un crash o riavvio riprendono dal checkpoint: i nodi completati non vengono rieseguiti, i
  loro output continuano a risolversi nelle espressioni a valle e gira solo il sottografo
  mancante. I node run orfani a metà esecuzione vengono chiusi come errore ("interrupted by
  restart") e rieseguiti dalla run ripresa.
- **Trigger di errore** (fase 2.5) — vedi la sezione Trigger: un workflow con trigger
  `error` parte quando un altro fallisce, ricevendo
  `{workflow_id, workflow_name, run_id, error, failed_node}` come `$trigger`.
- **Utilizzo token** — l'output dei nodi `llm.completion` e `llm.agent` include una chiave
  `_usage` (`{tokens_in, tokens_out, tokens_total}`, sommata sui passi dell'agente) quando
  il provider la riporta; `null` altrimenti. Il costo non viene stimato: non esiste ancora
  una tabella prezzi per modello nel progetto.
- **Alert su fallimenti ricorrenti** — dopo `GRAPH_WORKFLOW_RUN_FAILURE_ALERT_THRESHOLD`
  (default 3) esecuzioni fallite consecutive dello stesso workflow, parte una notifica
  in-app una sola volta (non ad ogni fallimento successivo).
- **Cache delle risposte** — `llm.completion` e ogni passo di `llm.agent` riusano la stessa
  cache delle risposte della chat (`RESPONSE_CACHE_ENABLED`, `RESPONSE_CACHE_TTL_SECONDS`,
  `RESPONSE_CACHE_MAX_ENTRIES`, più il livello fuzzy `SEMANTIC_CACHE_*` di Phase 26). Una
  richiesta `(model, messages, temperature, max_tokens)` identica salta del tutto il
  provider; l'output del nodo espone `_cache: "hit" | "semantic" | "miss"` accanto a
  `_usage`. I passi `llm.agent` che chiamano strumenti non vengono mai messi in cache
  (stessa regola della chat: una richiesta con `tools` non ottiene mai una chiave cache).

## Versioni ed esecuzioni

Il run panel ha una sezione **Versioni**: ogni snapshot con il suo timestamp e un
**Ripristina** a un click — il ripristino salva prima il grafo corrente come nuova
versione, quindi un rollback è sempre reversibile.

Ogni salvataggio crea uno snapshot di versione immutabile; puoi elencare le versioni e
tornare indietro. Ogni esecuzione salva il grafo eseguito, il contesto risolto e un record
per nodo (input, output, errore, tempi) ispezionabile a posteriori.

Poiché ogni valore viene persistito, l'editor non ha bisogno di una run live per mostrare
i dati: all'apertura di un workflow carica **l'ultimo output registrato di ogni nodo su
tutte le esecuzioni passate** (`GET /{id}/node-outputs`), quindi cliccando una freccia
vedi i campi e il payload transitati storicamente — con la nota "dati da un'esecuzione
passata" e il relativo orario. Una nuova run sostituisce quei valori con quelli live.

**Esegui da questo nodo (run parziali)**: seleziona un nodo e premi **▶ Esegui da questo
nodo** nell'inspector. Vengono eseguiti solo quel nodo e il sottografo a valle; ogni nodo
a monte viene "seminato" con il suo ultimo output persistito, così le espressioni
`$node.<id>.output.…` continuano a risolversi senza richiamare tool esterni. La run è
registrata con `trigger_type: partial` (API: `POST /{id}/run` con `start_node_id`).
Comodo mentre costruisci la coda di una pipeline la cui testa costosa è già stata eseguita.

**Riesegui una run (replay)**: ogni run terminata (completata, fallita o annullata) mostra
un pulsante **↻ Riesegui** nel pannello di dettaglio della vista Esecuzioni. Riavvia il
workflow con lo *stesso payload del trigger* di quella run sul grafo **corrente** — così,
dopo aver corretto un nodo, puoi riprodurre l'input originale con un clic e verificare la
correzione (API: `POST /v1/graph-workflows/runs/{rid}/replay`). Le run parziali non sono
rieseguibili (non hanno un payload di trigger completo) e restituiscono `409`.

**Prova espressione**: il pannello *Prova espressione* dell'inspector valuta qualsiasi
espressione (`={{ … }}`, `{{ … }}` o `=py:`) in sola lettura sui dati dell'ultima
esecuzione — `$node` dagli ultimi output persistiti, `$trigger` dalla run più recente — e
mostra il valore risolto o il messaggio d'errore inline (API:
`POST /{id}/preview-expression`). Utile per debuggare un percorso prima di usarlo in un
parametro.

**Export**: il pulsante *Esporta* (o `GET /{id}/export`) scarica il workflow come
snapshot JSON portabile (`{ kind, schema_version, name, description, graph, … }`). Dalla
fase 5.2 lo snapshot include anche l'array `secrets` — i **nomi** dei `$secrets.<name>`
referenziati dal grafo (i valori non viaggiano mai), così chi importa sa quali secret
ricreare nell'ambiente di destinazione. Dalla fase 7.2 lo snapshot include anche
`environments` — gli ambienti con nome del workflow (solo overlay `$vars` e alias
`$secrets`, mai valori; una `version` fissata non si applica nell'ambiente di
destinazione finché non viene ripromossa lì, perché i numeri di versione non sono
portabili tra workflow diversi).

**Import** (fase 5.2): il pulsante 📥 accanto a **Nuovo** apre un file `.workflow.json` —
esattamente il file prodotto da **Esporta** — e crea un nuovo workflow tramite l'endpoint
dedicato `POST /v1/graph-workflows/import`, aprendolo subito per la modifica. L'import è
**validato**: schema del grafo e limite di nodi sono vincolanti (400 in violazione),
mentre i problemi non bloccanti diventano warning mostrati come toast — tipi di nodo
sconosciuti (un tool o server MCP non disponibile qui), edge verso nodi mancanti e
riferimenti `$secrets` non definiti nel profilo. I campi presenti solo nell'export sono
accettati e ignorati.

**Condivisione tra workspace** (fase 5.2): un workflow si condivide in un workspace
(Fase 20) come conversazioni e documenti KB — `POST /v1/workspaces/{ws}/workflows`
(`{ workflow_id }`, ruolo editor + ownership), `GET` elenca i condivisi,
`DELETE /{ws}/workflows/{wid}` rimuove la condivisione. Ogni membro può **importarne una
copia** nel proprio profilo via `POST /{ws}/workflows/{wid}/import` — la copia si chiama
"… (shared)" e torna con gli stessi warning di validazione dell'import da file (i valori
dei `$secrets` non viaggiano mai).

### Metriche e osservabilità (fase 5.1)

`GET /v1/graph-workflows/stats` aggrega per workflow: conteggio run per esito
(completate / fallite / annullate), **tasso di successo** sulle run terminali, **durata
media** e i **totali di token LLM** sommati dalla chiave `_usage` riportata dai nodi
`llm.*`. La vista **Esecuzioni** li mostra come strip di dashboard (esecuzioni, tasso di
successo, durata media, token in/out) che segue il filtro workflow, e il dettaglio della
run mostra i token totali della run aperta accanto alla durata. Nessun costo inventato: i
token sono riportati così come sono (non esiste un listino per modello nel repo).

### Genera un workflow da una descrizione (fase 5.3)

Il pulsante 🪄 sopra l'elenco dei workflow apre il dialogo **"descrivi cosa vuoi"**:
`POST /v1/graph-workflows/generate` passa il catalogo dei nodi (tipi, output, nomi dei
parametri) all'LLM, che deve rispondere con un JSON completo `{name, description,
graph}`. Il dialogo espone lo stesso **selettore di modello** dei nodi `llm.*` più una
**catena di failover** opzionale (Impostazioni → Modelli), quindi la generazione può
usare qualsiasi provider/modello e ripiegare lungo la catena in caso di errore. La
risposta è **validata e normalizzata** — i tipi di nodo sconosciuti e le edge rotte
vengono scartati (con warning), un trigger mancante riceve un nodo `manual` in testa, i
nodi senza posizione ottengono un auto-layout a livelli — poi la bozza si apre
nell'editor per la revisione. Nulla viene eseguito finché non salvi e attivi tu.

La UI usa il gemello streaming `POST /v1/graph-workflows/generate/stream`, che emette
eventi SSE `log` a ogni fase — catalogo caricato (N tipi di nodo), modello chiamato,
risposta ricevuta (modello + stato cache), grafo validato (nodi/edge tenuti, warning),
trigger aggiunto, layout applicato — così il dialogo mostra un **log di avanzamento
live** invece del solo spinner, seguito da un evento `done` con la bozza (o `error` con
il motivo).

## Fase 6 — estensione del motore (trigger, cicli, composizione)

Implementata nella v3.1.0 (Phase 38):

- **Trigger `success` (6.1)** — lo specchio del trigger `error`: scatta quando la run di un
  altro workflow **si completa con successo** (filtro `config.workflow_id`, stesse guardie
  anti-loop). Payload: `{workflow_id, workflow_name, run_id, output}` con `output` =
  l'output dei nodi terminali della run completata — pipeline "A poi B" senza subworkflow.
- **Cron multipli per schedulazione (6.1)** — il pattern `cron` accetta una lista `crons`
  (nella UI una espressione per riga): la prossima esecuzione è la più vicina fra tutte le
  espressioni, per orari misti su un solo trigger.
- **Trigger `file.watch` (6.2)** — a polling (riusa il loop delle schedulazioni, niente
  inotify): osserva una sottocartella dello storage workspace (`config.path`) con pattern
  glob; scatta per ogni file creato/modificato con `$trigger = {path, event, size}`. Il
  primo polling inizializza solo lo snapshot; `config.interval` ha come minimo
  `GRAPH_WORKFLOW_WATCH_POLL_SECONDS` (default 60 s).
- **Trigger `email.inbound` (6.2)** — interroga una casella IMAP per i messaggi non letti:
  credenziali da `$secrets` (`password_secret` indica il nome del secret), filtri
  mittente/oggetto. `$trigger = {from, subject, body, attachments}`; gli allegati vengono
  salvati in `email_attachments/` nello storage, leggibili con `file.read`.
- **Nodo `while` (6.3)** — ciclo guidato da condizione (polling di API asincrone,
  paginazione a cursore) senza ricorsione di subworkflow. La `condition` viene
  **ri-valutata prima di ogni iterazione** con `$item` = output del corpo dell'iterazione
  precedente (input del nodo alla prima) e `$index` = numero di iterazione. Tetto
  obbligatorio: `maxIterations` (default 100), limite duro
  `GRAPH_WORKFLOW_WHILE_MAX_ITERATIONS` (default 1000). Output su `done`:
  `{items, count, capped}`.
- **Contratti dei sub-workflow (6.4)** — `input_schema` / `output_schema` (JSON Schema,
  sezione **Contratti** nel pannello run; viaggiano con export/import): il nodo
  `subworkflow` valida il payload prima della run figlia e l'output al ritorno. I workflow
  con contratto di input appaiono nella palette come nodi tipizzati **`workflow.<id>`** e
  il generatore LLM (fase 5.3) li vede nel catalogo, quindi può comporre workflow
  esistenti invece di rigenerarli.
- **Nodo `kb.search` (6.5)** — ricerca semantica sulla knowledge base dentro un workflow:
  `query` (espressione, default input del nodo), `top_k`, filtro `document_ids` opzionale.
  Output: `{results: [{text, score, source, chunk_index}], count}` — RAG nei workflow
  senza passare da un `llm.agent` generico.
- **Rate limiting per host (6.6)** — `http.request` (e `notify.webhook`) è limitato per
  host con finestra scorrevole di un minuto: `maxRequestsPerMinute` sul nodo e/o mappa
  globale `GRAPH_WORKFLOW_RATE_LIMITS` (`host=rpm` o oggetto JSON; vince il tetto più
  severo). Le richieste oltre soglia **aspettano, non falliscono**; l'attesa è riportata
  come `rate_limited_s` nell'output del nodo.

## Operatività e governance (fase 7)

**Retry dal nodo fallito** (7.1): le run fallite mostrano un pulsante **↺ Riprova**. A
differenza del Replay — che riparte da zero con il trigger originale sul grafo corrente —
il Retry crea una nuova run sullo **snapshot esatto del grafo della run di origine**,
seminata con gli output già salvati nel checkpoint: si ri-eseguono solo il nodo fallito e
il suo sottografo a valle (`POST /runs/{rid}/retry`, `409` se la run non è `failed`).
Retry e replay registrano `origin_run_id`, mostrato nel dettaglio della run.

**Ambienti** (7.2): la sezione **Ambienti** del pannello di esecuzione definisce ambienti
con nome come mappa JSON — `{"prod": {"vars": {...}, "secrets": {"TOKEN": "TOKEN_PROD"},
"version": 5}}`. Le `vars` sovrascrivono i `$vars` del workflow, i `secrets` rimappano gli
alias `$secrets.<alias>` su un altro segreto salvato (solo nomi, mai valori), `version`
fissa la versione del grafo eseguita in quell'ambiente. **⇧ Promuovi**
(`POST /{id}/environments/{env}/promote`) fissa la versione corrente — "promote to prod"
mentre l'editor continua a lavorare sul grafo corrente. L'ambiente si sceglie sulle run
manuali (campo `environment`) e nella config dei trigger schedule/webhook; ogni run
registra l'ambiente usato (badge nella vista Esecuzioni).

**Audit e ruoli di condivisione** (7.3): `GET /{id}/audit` restituisce il registro
attività del workflow (creazioni, modifiche, attivazioni, esecuzioni, approvazioni,
promozioni…), dal più recente. La condivisione in un workspace ora porta un **ruolo**:
`viewer` (ispeziona/importa), `editor` (può anche lanciare run — eseguite sotto il
profilo del proprietario), `approver` (può anche decidere le richieste `human.approval`).

**Metriche per nodo** (7.4): `GET /{id}/stats/nodes` aggrega lo storico per nodo —
esecuzioni per esito, tasso di errore, durata media/p50/p95, token LLM — ordinato dal
nodo peggiore. La nuova tab **Salute** della shell mostra la tabella e il registro audit.

**Approvazione da Telegram** (7.5): le notifiche `human.approval` con Telegram attivo
portano bottoni inline **✅ Approva / ❌ Rifiuta**; il bot verifica il collegamento
chat ↔ profilo e decide la richiesta come l'endpoint web (vince il primo scrittore), e la
run sospesa riprende in pochi secondi.

### Editor avanzato — diff, note, debug passo-passo (fase 8)

**Diff tra versioni (8.1)** — nella sezione **Versioni** del run panel, la riga *Confronta*
mette a confronto due versioni salvate (**Diff**): i nodi aggiunti si illuminano di verde,
quelli modificati di giallo, quelli rimossi sono elencati nella barra di diff. La posizione
di un nodo è ignorata di proposito (spostarlo non è una modifica). API:
`GET /{id}/versions/{a}/diff/{b}`.

**Note e riquadri (8.2)** — i pulsanti **📝 Nota** e **▢ Riquadro** posizionano note
adesive e riquadri di raggruppamento sul canvas (trascinabili, doppio clic per modificare,
vuoto = elimina). Vengono salvati con il grafo, versionati ed esportati, ma **il motore li
ignora completamente**: non vengono mai eseguiti.

**Debug passo-passo (8.3)** — **🐞 Debug** attiva la modalità debug; cliccando il pallino
di un nodo si imposta un **breakpoint**. **Avvia debug** crea la run in stato **`paused`**,
prima di eseguire qualsiasi nodo; poi **⏭ Passo** (esegue il nodo successivo e si ferma),
**▶ Continua** (fino al prossimo breakpoint o alla fine) e **⏹ Ferma** (annulla). Il nodo
in attesa è evidenziato in viola e la barra debug mostra il suo input risolto. API:
`POST /{id}/run` con `debug:true`, poi `POST /runs/{id}/debug`
(`{command, breakpoints?, input?}`); il campo `input` opzionale simula l'input del nodo
successivo (edit-the-pin). Si basa sul meccanismo di ripresa (fase 2.4): ogni comando
riprende dal checkpoint, esegue un nodo e si rimette in pausa. Le sessioni lasciate in
pausa oltre `GRAPH_WORKFLOW_DEBUG_MAX_PAUSE` (default 1 h) vengono annullate. Le run in
pausa mostrano un chip viola **paused** nella vista Esecuzioni.

### Workflow come strumenti dell'ecosistema (fase 9)

Un workflow può diventare un **componente** richiamabile da altri.

- **Pubblica come strumento (9.1)** — dai al workflow un **contratto di input**
  (pannello run → *Contratti*), spunta **Pubblica come strumento** e **attivalo**: diventa
  uno strumento `workflow__<id>` invocabile dai nodi **`llm.agent`**, dai nodi **`tool.*`**
  di altri workflow e dalla **chat**. L'invocazione lo esegue come una run normale (valgono
  metriche e audit) e ne restituisce l'output. Un limite di profondità
  (`GRAPH_WORKFLOW_TOOL_MAX_DEPTH`, default 3) impedisce la ricorsione infinita.
  `GET /tools` elenca gli strumenti pubblicati.
- **Server MCP del prodotto (9.2)** — gli stessi workflow sono raggiungibili da client MCP
  esterni (Claude Desktop, IDE) via `POST /v1/graph-workflows/mcp`, endpoint JSON-RPC 2.0
  (`initialize` / `tools/list` / `tools/call` / `ping`); una `tools/call` esegue il
  workflow inline (origine `mcp`).
- **Trigger `chat` (9.3)** — aggiungi un trigger **`chat`** e chiudi il grafo con un nodo
  **`chat.reply`**: `POST /v1/graph-workflows/{id}/chat` con `{ message, session_id? }`
  esegue il workflow con `$trigger = {session_id, message, history}` e restituisce la
  risposta. Lo stato della sessione persiste tra i turni (purga dopo
  `GRAPH_WORKFLOW_CHAT_SESSION_TTL`).
- **Import OpenAPI (9.4)** — `POST /v1/graph-workflows/openapi/import` (`spec` inline o
  `url`) trasforma ogni operazione in un nodo **`http.request`** preconfigurato (metodo,
  URL, query, auth mappata su `$secrets`), restituito non salvato da trascinare sul canvas.

### Test, dry-run e stima costi (fase 11)

Tratta il workflow come codice, dal pannello run → **Test & dry-run**:

- **Suite di test (11.1)** — salva un **caso di test**: payload `$trigger` fisso +
  **asserzioni** sull'output di un nodo (`equals`, `contains`, `json_path`, `schema`).
  **Esegui i test** lancia ogni caso come una run vera e osservabile e mostra verde/rosso
  per asserzione. Un nodo a effetto esterno (`http.request`, `db.query`,
  `notification.*`/`email.*`, `llm.*`) con un **output pinnato** (fase 3.2) rende il test
  deterministico — nessuna chiamata reale; senza pin il nodo esegue comunque per davvero.
- **Dry-run completo (11.2)** — **Esegui dry-run** simula l'intero grafo: ogni nodo a
  effetto esterno viene simulato (il suo pin, o un placeholder tipizzato) — **nulla di
  esterno avviene mai**. Il report mostra il percorso eseguito, gli output simulati e quali
  nodi avrebbero avuto un effetto reale. Da usare prima di attivare uno schedule su un
  grafo nuovo.
- **Stima costi (11.3)** — proiezione statica di token/mese: nodi `llm.*` del grafo ×
  media storica di token per run × frequenza dello schedule attivo. Solo token, nessun
  listino prezzi inventato.

### Budget, retention e oscuramento dati (fase 12)

Barriere di sicurezza prima di portare in produzione la combinazione schedule + LLM,
accanto ad audit trail e ruoli di condivisione (fase 7.3).

- **Budget e quote (12.1)** — imposta un tetto mensile di **token** e/o **esecuzioni** su
  un workflow (pannello run → **Budget e quote**, sotto Test & dry-run) e/o un tetto a
  livello di profilo (`GET/PUT /v1/graph-workflows/budget`) che si applica in aggiunta su
  tutti i workflow. L'utilizzo è calcolato sul mese solare UTC corrente dalla stessa
  cronologia già usata dalle statistiche fase 5.1 — nessun contatore da azzerare a mano,
  il periodo si rinnova da solo. Al raggiungimento di un tetto le nuove esecuzioni si
  fermano: un'esecuzione manuale viene rifiutata con un errore esplicito, e un trigger
  schedule/event che continua a scattare a budget esaurito si disabilita da solo dopo la
  consueta serie di fallimenti consecutivi (lo stesso meccanismo che già ritira un trigger
  rotto). Il superamento dell'80% di un tetto (configurabile via
  `GRAPH_WORKFLOW_BUDGET_WARN_PCT`) genera una notifica in-app una tantum per periodo.
- **Retention e oscuramento (12.2)** — assegna a un workflow una propria finestra di
  conservazione delle run in giorni, oppure lascia il default d'istanza
  (`GRAPH_WORKFLOW_RUNS_RETENTION_DAYS`, 0 = conserva per sempre); una pulizia periodica
  elimina le run terminate (completed/failed/cancelled) oltre la soglia — una run ancora
  in corso o in attesa di un umano non viene mai toccata. Per un nodo il cui output porta
  qualcosa di sensibile, elenca i percorsi puntati (es. `body.card_number`) nel campo
  **Oscura** dell'inspector: quei campi vengono mascherati come `***` ovunque l'output
  venga persistito, trasmesso live o esportato — ma il valore reale resta ciò che vede il
  nodo *successivo*, quindi un campo oscurato può ancora guidare la logica a valle durante
  l'esecuzione stessa.

### Copilot e workflow-as-code (fase 13)

- **Autocompletamento espressioni (13.1)** — digitando `$node.` in un campo espressione,
  l'inspector propone gli id dei nodi a monte di quello che stai modificando; scelto un
  id, `.` completa con i campi reali del suo output (da un output pinnato o dall'ultima
  esecuzione). `$vars.` e `$secrets.` completano allo stesso modo contro le variabili
  dichiarate e i *nomi* dei secret del workflow — mai i valori — e `$item`/`$index`
  compaiono per un nodo dentro un corpo for/repeat.
- **Spiega / ripara (13.2)** — quando una run fallisce, il nodo fallito nel pannello run
  mostra un pulsante **Spiega / ripara**: invia tipo, parametri correnti, input ricevuto
  ed errore del nodo all'LLM, che risponde con una causa in linguaggio semplice e, se
  sicuro di una correzione concreta, un oggetto parametri corretto mostrato come diff.
  Nulla viene applicato automaticamente — **Applica la correzione** la unisce al nodo sul
  canvas (va comunque salvato normalmente), **Scarta** la ignora.
- **Sincronizzazione Git delle definizioni (13.3)** — collega un workflow a un repo Git
  (pannello run → Versioni → **Sincronizzazione Git**: URL, branch, nome di un secret con
  il token di accesso, percorso opzionale nel repo) e ogni versione salvata da quel
  momento viene committata come JSON — un commit per versione, messaggio con versione e
  autore. **Pull ora** recupera il branch e, se il file è cambiato (es. una PR è stata
  fusa), lo importa come nuova versione **bozza** — non sovrascrive mai il grafo live,
  quindi la rivedi/ripristini come qualsiasi altra versione.

### Esecuzione remota e scalabilità (fase 14)

**Runner remoti (fase 14.1).** Alcuni compiti devono avvenire altrove rispetto al
processo backend: un'API interna raggiungibile solo dalla rete del cliente, un database
non esposto pubblicamente, un nodo `code` pesante che richiede una macchina più potente,
inferenza locale su una GPU. Da **Graph workflows → Runner** registra un runner (nome,
etichette come `gpu`/`internal-network`/`dmz` e un'eventuale lista di tipi di nodo
consentiti) — ricevi un token monouso, mostrato una sola volta. Avvia il processo agente
ovunque abbia accesso in uscita verso il backend:

```
SIBYL_RUNNER_TOKEN=<token> python -m app.runner.agent
```

Invia heartbeat e fa long-poll per ricevere lavoro; non serve aprire alcuna porta in
ingresso. Assegna a un nodo un'etichetta **runOn** (impostazioni avanzate) corrispondente
a un'etichetta del runner ed esegue lì invece che sul backend — solo per i tipi di nodo
che non richiedono contesto backend (`http.request`, `code`, `db.query`, `set`, `if`,
`switch`, `merge`, `filter`, `aggregate`, `batch`, `wait`, `queue.publish`); tutto ciò che
referenzia `$secrets` nei parametri arriva al runner già risolto al valore letterale, mai
il vault. Nessun runner corrispondente online entro il timeout: **runOnFallback** `fail`
(predefinito) fa fallire il nodo come un errore qualsiasi (retry/On error si applicano
comunque), `local` lo esegue invece sul backend.

**Sandbox del nodo `code` (fase 14.2).** Niente da attivare — il nodo `code` è sempre
stato eseguito in un sottoprocesso isolato (limiti di CPU/memoria/tempo, nessuna rete),
sul backend e allo stesso modo su un runner remoto.

**Scale-out del motore (fase 14.3).** Dietro le quinte, ogni run è "in lease" all'istanza
di processo che lo esegue e il lease si rinnova da solo mentre il run è attivo; un lease
lasciato da un crash è libero per l'istanza successiva (anche riavviata) — lo stesso
meccanismo di checkpoint/resume della fase 2.4. Niente da configurare su un deployment a
istanza singola; è il punto d'aggancio per un futuro deployment multi-replica/Postgres.

**Trigger a coda di messaggi (fase 14.4).** Un nodo **Queue publish** invia un messaggio a
un topic; un trigger **Queue consume** su un altro (o lo stesso) workflow si attiva una
volta per messaggio ricevuto, con `$trigger = {message, topic, headers}`. Di default i
messaggi sono persistiti (`GRAPH_WORKFLOW_QUEUE_DRIVER=db`), quindi nulla va perso a un
riavvio; nessun broker esterno è richiesto. Un broker reale (RabbitMQ/Kafka/MQTT) potrà
essere collegato in futuro come sostituto diretto, senza toccare nodo o trigger.

**CLI (fase 14.5).** `python -m app.cli.sibyl_wf` guida la stessa API REST da terminale o
pipeline CI — `run <id>`, `export`/`import`, `test <id> <node_id>`, `logs <run_id>` —
autenticato con un token bearer (`SIBYL_API_KEY`).

### Connettori e nodi multimodali (fase 15)

**Connettori curati (fase 15.1).** Una categoria di palette **Connettori** offre nodi
`connector.<servizio>.<operazione>` messi a punto a mano sopra `http.request`, con
endpoint, autenticazione e payload già cablati: **Slack** / **Discord** (invia messaggio),
**GitHub** / **GitLab** (crea issue), **Jira** (crea issue), **Google Sheets** (append /
lettura). Le credenziali arrivano da `$secrets` (es. il campo token impostato a
`={{ $secrets.SLACK_TOKEN }}`), mai scritte a mano. Poiché sotto *sono* `http.request`,
valgono retry/backoff, test del nodo, pin e rate limit per host; l'output è quello HTTP più
`{operation}`.

**`ssh.exec` (fase 15.2).** Esegue un comando su un host remoto via SSH — chiave o password
da `$secrets`, allow-list degli host via `GRAPH_WORKFLOW_SSH_ALLOWED_HOSTS` (vuoto =
qualsiasi), timeout per comando. Output `{stdout, stderr, exit_code}`; un'uscita diversa da
zero solleva errore (retry / In errore si applicano) a meno che non si imposti **Consenti
uscita diversa da zero**.

**`browser` (fase 15.3).** Scraping/controlli con browser headless (Playwright): apri una
URL, attendi facoltativamente un selettore CSS, quindi estrai **testo**, un **attributo** o
uno **screenshot** (salvato nello storage del workspace, leggibile da `file.*`). Gira in un
thread con timeout per azione; richiede `playwright` (+ un browser) nell'immagine.

**Trigger `rss.read` (fase 15.4).** Interroga un feed RSS/Atom e avvia **una run per ogni
nuova voce**, deduplicata per guid, con `$trigger = {title, link, published, summary,
guid}`. Riusa il loop di polling di file.watch/coda; il primo poll fa solo il seed
dell'insieme visto (`GRAPH_WORKFLOW_RSS_MAX_ENTRIES` limita gli scatti per poll). Si collega
con `{url, interval}`. Ideale per flussi "notizie → LLM → notifica".

**`doc.convert` (fase 15.5).** Converte un documento PDF/DOCX/HTML/PPTX/… dallo storage del
workspace in **markdown** via markitdown, output `{markdown, chars, path}`; `path` ricade
sull'input del nodo, concatenandosi direttamente da `file.watch` `$trigger.path`. Gli altri
nodi multimediali (`audio.transcribe`, `image.ocr`, `image.generate`, `tts`) dipendono dal
supporto del provider e sono rinviati.

### Stato e semantica di esecuzione (fase 16)

**Stato persistente tra le esecuzioni (fase 16.1).** Tre nodi della categoria **Data** leggono e
scrivono un archivio chiave/valore per workflow che **sopravvive tra le esecuzioni**: `state.get` →
`{key, value, found}` (con un `default` opzionale quando la chiave manca o è scaduta), `state.set`
(il cui `value` per default è l'input del nodo) e `state.increment` (somma numerica atomica,
restituisce il nuovo valore — ideale per contatori e finestre di rate). Con `ttlSeconds` dai a una
chiave una scadenza; una chiave scaduta si legge come assente. L'archivio è visibile e modificabile
dal pannello di esecuzione — `GET/PUT/DELETE /v1/graph-workflows/{id}/state` — con le modifiche
manuali registrate nell'audit, e **non è mai incluso in un export** (vive in una tabella dedicata,
non nella definizione del workflow).

**Idempotenza dei trigger (fase 16.2).** Imposta un'espressione `dedupKey` su un trigger **webhook**
o **event** (es. `{{ $trigger.order_id }}`): la stessa chiave consegnata due volte entro
`dedupWindowSeconds` restituisce il `run_id` **originale** (HTTP 200, `deduped: true`) invece di
avviare una seconda esecuzione — elaborazione exactly-once per sistemi che ritentano le consegne.
Le chiavi sono salvate con TTL; la finestra di default proviene da
`GRAPH_WORKFLOW_DEDUP_DEFAULT_WINDOW_SECONDS`.

**Compensazioni / saga (fase 16.3).** Collega un arco `compensate` da un nodo con effetti collaterali
a un piccolo sottografo di rollback. Se l'esecuzione **fallisce più a valle**, il motore percorre i
nodi completati in **ordine inverso** ed esegue il ramo di compensazione di ciascuno, alimentato con
l'output del nodo stesso (es. rilasciare lo stock riservato quando l'addebito successivo fallisce).
Le esecuzioni di nodo di compensazione sono contrassegnate con `compensation: true` nello stream
live; un errore in una compensazione marca l'esecuzione come `failed` con un errore composto.
Completamente opt-in — un grafo senza arco `compensate` non è influenzato.

**Priorità di esecuzione (fase 16.4).** Una `priority` su un'esecuzione (dalla config del trigger
`priority` o dall'API di avvio `priority`) fa sì che la coda per workflow promuova prima le
esecuzioni a priorità più alta, FIFO a parità di priorità — un'esecuzione interattiva può passare
davanti a un backfill batch.

## Esempi dettagliati per funzionalità

Ricette complete e riproducibili, una per area del motore. Ogni esempio dà **obiettivo**,
la **catena del grafo**, la **configurazione nodo per nodo** con valori ed espressioni
concreti, l'**output atteso** e la **funzionalità dimostrata**. Sono pensate per essere
ricostruite a mano sul canvas o adattate: sostituisci gli URL/le città/le API con i tuoi.
Molte hanno un gemello importabile con un click nella galleria ✨ (vedi
[grafi di esempio](../examples/graph-workflows.md)).

> **Convenzione** — dove vedi `={{ … }}` è un'espressione (valutata); dove vedi un valore
> nudo è un letterale. Gli id dei nodi (`rss`, `api`, `triage`…) sono quelli scelti
> nell'inspector e usati nei percorsi `$node.<id>.output`.

### 1. Digest RSS mattutino — trigger schedule + tool + LLM

**Obiettivo:** ogni mattina alle 8:00 riassumere la prima pagina di un feed in cinque punti
e comporre un oggetto digest titolato.

**Grafo:** `schedule → tool.fetch_rss → llm.completion → set`

**Nodi:**
- `schedule` (trigger `schedule`) — pattern **Giornaliero**, orario `08:00`. Ricorda: scatta
  solo con il workflow **Attivo**.
- `rss` (`tool.fetch_rss`) — `url`: `={{ $vars.FEED }}` (definisci `FEED =
  https://hnrss.org/frontpage` nel pannello *Variabili*).
- `summary` (`llm.completion`) — modello scelto dal selettore; `prompt`:
  ```
  Riassumi queste notizie in 5 punti concisi in italiano:
  ={{ $node.rss.output.result }}
  ```
- `digest` (`set`) — costruisce l'oggetto:
  - `title` → `Digest del ={{ $now }}`
  - `body` → `={{ $node.summary.output.content }}`

**Output atteso:** `{ title: "Digest del 2026-07-20…", body: "• …\n• …" }`.

**Dimostra:** trigger schedule, passaggio output→input via `$node.<id>.output`, `$vars`,
interpolazione di stringa, catena trigger → azione → IA → dati.

### 2. Webhook → risposta dalla knowledge base (RAG) — `$trigger` + firma HMAC

**Obiettivo:** esporre un URL pubblico che risponde a una domanda **solo** con i passaggi
recuperati dalla KB.

**Grafo:** `webhook → kb.search → llm.completion → set`

**Nodi:**
- `webhook` (trigger `webhook`) — dopo il salvataggio, genera il segreto di firma con
  **Ruota segreto** (mostrato una sola volta).
- `search` (`kb.search`) — `query`: `={{ $trigger.question }}`, `top_k`: `5`.
- `answer` (`llm.completion`) — `prompt`:
  ```
  Rispondi in italiano usando SOLO questi passaggi. Se non bastano, dillo.
  Domanda: ={{ $trigger.question }}
  Passaggi: ={{ $node.search.output.results }}
  ```
- `out` (`set`) — `answer` → `={{ $node.answer.output.content }}`.

**Come chiamarlo** (workflow Attivo):
```bash
BODY='{"question":"come configuro SMTP?"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SEGRETO" -hex | sed 's/^.* //')
curl -X POST https://tuo-host/api/v1/wf/hooks/$TOKEN \
     -H "X-Signature: sha256=$SIG" -H 'Content-Type: application/json' -d "$BODY"
```

**Dimostra:** trigger webhook, lettura di `$trigger.<campo>`, RAG con `kb.search`, protezione
HMAC (una richiesta senza header valido è rifiutata con 401 prima di essere interpretata).

### 3. Branch condizionale — `if` + espressioni whitelisted

**Obiettivo:** controllare una pagina web e ramificare a seconda che compaia una parola
chiave.

**Grafo:** `schedule → tool.read_url → if → set (vero) | set (falso)`

**Nodi:**
- `fetch` (`tool.read_url`) — `url`: `={{ $vars.PAGE }}`.
- `check` (`if`) — `condition`:
  `={{ 'saldi' in lower($node.fetch.output.result) }}`.
- `hit` (`set`, ramo **true**) — `alert` → `Trovato "saldi" alle ={{ $now }}`.
- `miss` (`set`, ramo **false**) — `status` → `nessuna variazione`.

**Output atteso:** parte un solo ramo; il nodo del ramo non scelto è registrato come
`skipped`.

**Dimostra:** routing con `if`, operatore `in`, funzione `lower()`, rami mutuamente
esclusivi.

### 4. Chiamata API con retry e ramo di errore — try/catch sul canvas

**Obiettivo:** chiamare un'API esterna, riprovare due volte, e **avvisare** solo se ogni
tentativo fallisce.

**Grafo:** `manual → http.request → set (main) | notify.telegram (error)`

**Nodi:**
- `api` (`http.request`) — `method` `GET`, `url` `={{ $vars.API_URL }}`, `timeout` `60`.
  Sezione **Avanzate**: **Tentativi** `2`, **Backoff** `2` s **Esponenziale**, **In caso di
  errore → Instrada sul ramo di errore**.
- `ok` (`set`, uscita **main**) — `status` → `={{ $node.api.output.status }}`,
  `data` → `={{ $node.api.output.json }}`.
- `alert` (`notify.telegram`, uscita **error**) — `text`:
  `API non raggiungibile: ={{ $node.api.output.error }}`.

**Output atteso:** al successo scorre `main` con `{ status, ok, headers, json, text }`; a
fallimento esaurito, `{ error, input }` scorre sull'handle `error` e il ramo `main` è
saltato. Il nodo `api` resta registrato come **errore** anche quando instrada sul ramo error.

**Dimostra:** `http.request`, retry con backoff esponenziale, la politica *In caso di
errore → ramo di errore*, `$vars`.

### 5. Routing multi-ramo — `switch`

**Obiettivo:** instradare per canale a una fra tre code.

**Grafo:** `manual → switch → set | set | set`

**Nodi:**
- `route` (`switch`) — `value`: `={{ default($trigger.channel, 'a') }}`; `cases`:
  `["a","b","c"]`. Handle di uscita: `a`, `b`, `c`, `default`.
- tre `set` collegati ai rispettivi handle.

**Prova:** in **Payload di esecuzione** metti `{"channel":"b"}` → parte solo il ramo `b`; con
un valore fuori lista scatta `default`.

**Dimostra:** `switch` multi-caso, `default()`, payload di esecuzione manuale come `$trigger`.

### 6. Ciclo for-each su un array — handle `loop` / `done`, `$item` / `$index`

**Obiettivo:** per ogni URL di un elenco, scaricarlo e raccogliere i titoli.

**Grafo:** `manual → set (lista) → for → (loop) tool.read_url → set` · `(done) set`

**Nodi:**
- `urls` (`set`) — `list` → `={{ ['https://a.dev','https://b.dev'] }}` (espressione da sola:
  resta una lista nativa).
- `loop` (`for`) — `items`: `={{ $node.urls.output.list }}`.
- corpo, collegato all'handle **`loop`**:
  - `get` (`tool.read_url`) — `url`: `={{ $item }}` (dentro il corpo si usa `$item`/`$index`,
    **non** `$node.loop.output`).
  - `title` (`set`) — `t` → `={{ slice($node.get.output.result, 0, 80) }}`.
- continuazione, collegata all'handle **`done`**:
  - `all` (`set`) — `titles` → `={{ $node.loop.output.items }}`.

**Output atteso:** su `done`, `loop` produce `{ items: [...], count: 2 }`.

**Dimostra:** `for`, scope per-iterazione (`$item`/`$index`), separazione corpo (`loop`) /
continuazione (`done`), raccolta dei risultati.

### 7. Ciclo guidato da condizione — `while` (paginazione / polling)

**Obiettivo:** scaricare pagine finché l'API restituisce un cursore.

**Grafo:** `manual → while → (loop) http.request → set` · `(done) aggregate`

**Nodi:**
- `pager` (`while`) — `condition`:
  `={{ $index == 0 or $item.next != null }}`, `maxIterations`: `50`.
- corpo (`loop`):
  - `page` (`http.request`) — `url`:
    `={{ $vars.API }}?cursor=={{ default($item.next, '') }}`.
  - `norm` (`set`) — `items` → `={{ $node.page.output.json.items }}`,
    `next` → `={{ $node.page.output.json.next }}` (diventa `$item` dell'iterazione dopo).
- `flat` (`aggregate`, su `done`) — `op` `concat` sul campo `items`.

**Output atteso:** su `done`, `{ items, count, capped }` (`capped: true` se si tocca il
tetto).

**Dimostra:** `while` (condizione ri-valutata prima di ogni giro con `$item` = output del
corpo precedente), tetto `maxIterations`, `aggregate`.

### 8. Pipeline dati — `set` + `filter` + `aggregate` con la via `=py:`

**Obiettivo:** tenere solo gli ordini grandi e sommarne i totali.

**Grafo:** `manual → set → filter → aggregate → set`

**Nodi:**
- `orders` (`set`) — `list` →
  `={{ [{'id':1,'total':40},{'id':2,'total':150},{'id':3,'total':300}] }}`.
- `big` (`filter`) — `items`: `={{ $node.orders.output.list }}`; maschera **keep** con la
  via di fuga sandbox: `=py:[o['total'] > 100 for o in input]`.
- `sum` (`aggregate`) — `op` `sum` sul campo `total`.
- `out` (`set`) — `total` → `={{ $node.sum.output.result }}` (`450`).

**Dimostra:** `filter` con maschera booleana, escape hatch `=py:` (comprehension reale),
`aggregate` (`sum/avg/min/max/count/concat`).

### 9. Composizione con contratto — `subworkflow` + `input_schema`/`output_schema`

**Obiettivo:** riusare un workflow "arricchisci cliente" come passo di un altro,
validandone ingresso e uscita.

**Prerequisito** — nel workflow figlio, pannello run → **Contratti**:
- `input_schema`: `{"type":"object","required":["email"],"properties":{"email":{"type":"string"}}}`
- `output_schema`: `{"type":"object","required":["score"]}`

**Grafo (padre):** `manual → subworkflow → set`

**Nodi:**
- `enrich` (`subworkflow`) — **Workflow**: seleziona il figlio dal menu; `payload`:
  `={{ {'email': $trigger.email} }}`. Il payload è validato contro `input_schema` **prima**
  della run figlia; l'output al ritorno contro `output_schema`.
- `out` (`set`) — `score` → `={{ $node.enrich.output.output.score }}`.

**Output atteso:** `{ run_id, workflow_id, status, output }` — `output` è l'output del nodo
terminale del figlio. Annidamento max 5 livelli; l'auto-ricorsione fa fallire la run.

**Dimostra:** `subworkflow`, contratti I/O JSON Schema, run figlia osservabile
(`trigger_type: subworkflow`). Con un `input_schema`, il figlio appare anche come nodo
tipizzato **`workflow.<id>`** nella palette.

### 10. Cancello di approvazione umana — `human.approval`

**Obiettivo:** fermare un deploy finché una persona non approva.

**Grafo:** `manual → human.approval → notify.inapp (approved) | notify.inapp (rejected)`

**Nodi:**
- `gate` (`human.approval`) — `title`: `Deploy ={{ $trigger.subject }}`, `message`:
  `Confermi il rilascio?`, `timeout`: `86400` (24 h), `onTimeout`: `reject`,
  `telegram`: `true` (bottoni inline in chat).
- `go` (`notify.inapp`, handle **approved**) — `title`: `Deploy approvato`.
- `stop` (`notify.inapp`, handle **rejected**) — `title`: `Deploy respinto`.

**Come decidere:** la run entra in stato **`waiting`** (chip viola). Aprila da
**Esecuzioni** → **✓ Approva / ✕ Rifiuta** (con commento) oppure via API:
```
POST /v1/graph-workflows/approvals/{aid}/decision  {"approved": true, "comment": "ok"}
```

**Output atteso:** `{ approved, status, comment, decided_by }` sul ramo scelto. L'attesa
sopravvive ai riavvii (checkpoint) e **non** occupa uno slot di concorrenza.

**Dimostra:** HITL, stato `waiting`, handle `approved`/`rejected`, decisione web o Telegram.

### 10a. Form di approvazione spesa — `human.input`

**Obiettivo:** raccogliere un importo + categoria validati prima di continuare.

**Grafo:** `manual → human.input → notify.inapp (submitted) | notify.inapp (timeout)`

**Nodi:**
- `form` (`human.input`) — `title`: `Expense approval`, `schema`: `{ "type": "object",
  "required": ["amount", "category"], "properties": { "amount": {"type": "number"},
  "category": {"type": "string", "enum": ["travel", "meals", "software", "other"]} } }`,
  `timeout`: `86400`, `onTimeout`: `branch`.
- `logged` (`notify.inapp`, handle **submitted**) — il body usa
  `={{ $node.form.output.data.category }}: ={{ $node.form.output.data.amount }}`.
- `expired` (`notify.inapp`, handle **timeout**).

**Come compilarlo:** la run entra in stato **`waiting`**; aprila da **Esecuzioni** — i
campi vengono renderizzati a partire dallo schema — oppure via API:
```
POST /v1/graph-workflows/approvals/{aid}/submit  {"data": {"amount": 42, "category": "travel"}}
```

**Output atteso:** `{ data, status, comment, decided_by }` sul ramo `submitted` — `data`
viene validato rispetto allo `schema` lato server prima di essere accettato.

**Dimostra:** raccolta form HITL, validazione JSON-Schema, handle `submitted`/`timeout`.

### 10b. Attesa pagamento — `wait.event`

**Obiettivo:** sospendere una run di checkout finché un provider di pagamento esterno non
la conferma.

**Grafo:** `manual → wait.event → notify.inapp (main) | notify.inapp (timeout)`

**Nodi:**
- `wait` (`wait.event`) — `correlationId`: `={{ $trigger.order_id }}`, `timeout`: `3600`,
  `onTimeout`: `branch`.
- `paid` (`notify.inapp`, handle **main**) — body: `={{ $node.wait.output }}`.
- `expired` (`notify.inapp`, handle **timeout**).

**Come consegnarlo:** un sistema esterno (o un test manuale) esegue una POST verso il
correlation id:
```
POST /v1/graph-workflows/events/ord-123  {"payload": {"paid": true}}
```

**Output atteso:** il `payload` consegnato diventa l'output del nodo sul ramo `main`.

**Dimostra:** consegna evento per correlation id, veri callback asincroni senza polling.

### 11. Triage ticket — `llm.classify` + `switch` + `file.write` CSV

**Obiettivo:** etichettare un ticket con struttura garantita, instradarlo e loggarlo.

**Grafo:** `manual → llm.classify → switch → notify.inapp ×3` (+ `file.write`)

**Nodi:**
- `triage` (`llm.classify`) — `input`: `={{ $trigger.text }}`; `categories`:
  `billing, bug, question`. Una risposta fuori lista solleva errore (quindi valgono i
  retry).
- `route` (`switch`) — `value`: `={{ $node.triage.output.category }}`; `cases`:
  `["billing","bug","question"]`.
- tre `notify.inapp` sui rispettivi handle.
- `log` (`file.write`) — `path`: `tickets/triage-log.csv`, `format`: `csv`, `append`: `true`,
  `content`: `={{ {'cat': $node.triage.output.category, 'text': $trigger.text} }}`.

**Prova:** payload `{"text":"la mia fattura è sbagliata"}` → categoria `billing`.

**Dimostra:** `llm.classify` (output `{category, confidence}` garantito), `switch` sul
risultato, `file.write` CSV in append nello storage di workspace.

### 12. Estrazione strutturata — `llm.extract` con JSON Schema

**Obiettivo:** estrarre campi tipizzati da testo libero.

**Grafo:** `manual → llm.extract → db.query`

**Nodi:**
- `parse` (`llm.extract`) — `input`: `={{ $trigger.text }}`; `schema`:
  ```json
  {
    "type": "object",
    "required": ["name", "amount"],
    "properties": {
      "name":   {"type": "string"},
      "amount": {"type": "number"},
      "due":    {"type": "string"}
    }
  }
  ```
- `save` (`db.query`) — `driver`: `sqlite`, `database`: `invoices.db`,
  `query`: `INSERT INTO invoices(name, amount, due) VALUES (?,?,?)`,
  `params`: `={{ [$node.parse.output.data.name, $node.parse.output.data.amount, $node.parse.output.data.due] }}`.

**Output atteso:** `parse` → `{ data: {...}, model, _usage }` (le `required` di primo livello
sono verificate; una risposta non conforme solleva errore). `save` → `{ rows, count,
rowcount }`.

**Dimostra:** `llm.extract` con JSON Schema, `db.query` parametrizzato (placeholder `?` per
sqlite; il file vive nello storage di workspace).

### 13. Query su Postgres con credenziali sicure — `db.query` + `$secrets`

**Obiettivo:** leggere righe da Postgres senza mai mettere il DSN nel grafo.

**Prerequisito:** pannello run → **Secrets** → aggiungi `PG_DSN` (cifrato a riposo, mai
esportato).

**Grafo:** `schedule → db.query → notify.email`

**Nodi:**
- `q` (`db.query`) — `driver`: `postgres`, `dsn`: `={{ $secrets.PG_DSN }}`,
  `query`: `SELECT id, email FROM users WHERE created_at > $1`,
  `params`: `={{ [$vars.SINCE] }}` (placeholder `$1…` per postgres).
- `mail` (`notify.email`) — `to`: `={{ $vars.OPS }}`, `subject`: `Nuovi utenti`,
  `body`: `={{ $node.q.output.count }} nuovi: ={{ $node.q.output.rows }}`.

**Dimostra:** `db.query` postgres, segreti cifrati (`$secrets`, risolti solo durante la run,
`***` nel *Prova espressione*), placeholder parametrizzati.

### 14. Broadcast su tutti i canali — `notify.*` in parallelo

**Obiettivo:** consegnare un messaggio a in-app, Telegram, email e webhook, con degrado
elegante dei canali non configurati.

**Grafo:** `manual → set → notify.inapp + notify.telegram + notify.email + notify.webhook`

**Nodi:**
- `msg` (`set`) — `text` → `={{ $trigger.text }}`.
- i quattro `notify.*` collegati in parallelo a `msg`. Su Telegram/email/webhook metti **In
  caso di errore → Continua sul ramo principale**, così un canale non configurato (nessuna
  chat, niente SMTP) non fa fallire la run; la campanella in-app funziona sempre.
- `notify.telegram` con `parse_mode`: `Markdown` se `text` arriva da un nodo `llm.*` in
  CommonMark (il `**grassetto**` è normalizzato nel `*grassetto*` di Telegram).

**Dimostra:** fan-out parallelo, i quattro canali di notifica, la politica *Continua* per la
tolleranza ai guasti.

### 15. Hub di alerting centralizzato — trigger `error`

**Obiettivo:** un workflow guardiano che avvisa quando **qualsiasi altro** workflow
fallisce.

**Grafo:** `error → set → notify.telegram`

**Nodi:**
- trigger `error` — pannello run → **＋ error**; lascia `config.workflow_id` **vuoto** per
  reagire a *ogni* fallimento (o mettine uno per osservarne uno solo). Attiva il workflow.
- `fmt` (`set`) — `text` →
  `❌ ={{ $trigger.workflow_name }} nodo ={{ $trigger.failed_node }}: ={{ $trigger.error }}`.
- `send` (`notify.telegram`) — `text`: `={{ $node.fmt.output.text }}`.

**Output atteso:** a ogni run fallita altrove, questo parte con
`$trigger = {workflow_id, workflow_name, run_id, error, failed_node}`.

**Dimostra:** trigger `error`, protezione anti-loop (non reagisce ai propri fallimenti, le
run da trigger error non cascatano). Speculare: il trigger `success` per pipeline "A poi B".

### 16. Agente autonomo dentro una pipeline — `llm.agent`

**Obiettivo:** delegare un obiettivo aperto al loop ad agente (con tool integrati + MCP +
custom) e consegnarne la risposta.

**Grafo:** `manual → llm.agent → notify.inapp`

**Nodi:**
- `agent` (`llm.agent`) — modello dal selettore; **Failover chain** opzionale; `goal`:
  `={{ default($trigger.goal, 'Ricerca le novità su X e riassumile') }}`; `max_steps`: `8`.
- `bell` (`notify.inapp`) — `body`: `={{ $node.agent.output.content }}`.

**Output atteso:** `{ content, _usage, _cache }`; `_usage` somma i token su tutti i passi
dell'agente. Un fallback riuscito è persistente (i passi seguenti partono dal modello che ha
funzionato).

**Dimostra:** autonomia inseribile dove serve, accesso all'intero registro tool dentro un
grafo deterministico, `_usage`/failover.

### 17. Ambienti dev/prod senza duplicare il grafo — `environments` + promote

**Obiettivo:** lo stesso grafo con endpoint e credenziali diversi tra prod e dev.

**Setup** — pannello run → **Ambienti**:
```json
{
  "prod": { "vars": {"API": "https://api.example.com"},
            "secrets": {"TOKEN": "TOKEN_PROD"}, "version": 5 },
  "dev":  { "vars": {"API": "https://staging.example.com"},
            "secrets": {"TOKEN": "TOKEN_DEV"} }
}
```
Un nodo legge `={{ $vars.API }}` e `={{ $secrets.TOKEN }}`: l'overlay dell'ambiente
sovrascrive `$vars` e rimappa gli alias `$secrets` (solo nomi, mai valori).

**Promuovere:** **⇧ Promuovi** (`POST /{id}/environments/prod/promote`) fissa la versione
corrente su `prod`, mentre continui a lavorare sul grafo. Scegli l'ambiente su una run
manuale (campo `environment`) o nella config di un trigger; ogni run ne registra il badge.

**Dimostra:** ambienti con nome, overlay `$vars`/alias `$secrets`, pinning di versione,
"promote to prod".

### 18. Debug passo-passo con breakpoint — modalità Debug (fase 8.3)

**Obiettivo:** ispezionare l'input risolto nodo per nodo prima che venga eseguito.

**Passi:**
1. **🐞 Debug** attiva la modalità; clicca il pallino di un nodo per porvi un **breakpoint**.
2. **Avvia debug** — la run nasce in stato **`paused`**, prima di ogni nodo (`POST /{id}/run`
   con `debug:true`).
3. **⏭ Passo** esegue il nodo successivo e si rimette in pausa; **▶ Continua** va fino al
   prossimo breakpoint; **⏹ Ferma** annulla (`POST /runs/{id}/debug` con
   `{command, breakpoints?, input?}`).
4. Il nodo in attesa è viola e la barra debug mostra il suo **input risolto**; il campo
   `input` opzionale simula quell'input (edit-the-pin).

**Dimostra:** debug basato sul meccanismo di ripresa (ogni comando riprende dal checkpoint,
esegue un nodo, si rimette in pausa); le sessioni in pausa oltre
`GRAPH_WORKFLOW_DEBUG_MAX_PAUSE` (default 1 h) vengono annullate.

### 19. Il workflow diventa uno strumento — pubblica come tool + trigger `chat` (fase 9)

**Obiettivo:** rendere un workflow richiamabile da `llm.agent`, dalla chat e da client MCP
esterni.

**Come tool (9.1):** dai al workflow un **contratto di input** (pannello run → *Contratti*),
spunta **Pubblica come strumento** e **attivalo**. Diventa `workflow__<id>`, invocabile dai
nodi `llm.agent`/`tool.*` di altri workflow e dalla chat; ogni invocazione è una run normale
(metriche + audit). Limite di profondità `GRAPH_WORKFLOW_TOOL_MAX_DEPTH` (default 3).

**Come chatbot (9.3):**
- **Grafo:** `chat → llm.completion → chat.reply`
- `reply` (`chat.reply`) — `text`: `={{ $node.<llm>.output.content }}`.
- Chiama: `POST /v1/graph-workflows/{id}/chat` con `{ "message": "ciao", "session_id": "s1" }`.
  Il grafo riceve `$trigger = {session_id, message, history}` e la sessione persiste tra i
  turni (purga dopo `GRAPH_WORKFLOW_CHAT_SESSION_TTL`).

**Via MCP (9.2):** lo stesso workflow è raggiungibile da Claude Desktop/IDE via
`POST /v1/graph-workflows/mcp` (JSON-RPC 2.0: `initialize` / `tools/list` / `tools/call`).

**Dimostra:** workflow-as-tool con anti-ricorsione, trigger `chat` + `chat.reply` con stato
di sessione, server MCP del prodotto.

### 20. Pianificazione, SLA e navigator (fase 17)

Gestire decine di workflow senza doverli sorvegliare. Tutto si configura sul workflow con
`PATCH /v1/graph-workflows/{id}`:

- **Calendari e finestre (17.1):** metti un fuso sul trigger `schedule` (`"tz": "Europe/Rome"`)
  così ogni pianificazione scatta nel proprio fuso. Salta le festività con
  `"skip_dates": ["2026-12-25"]` (sulla pianificazione o sul workflow). Aggiungi finestre di
  blackout sul workflow: `blackout = {"windows": [{"start":"01:00","end":"02:30","days":[0,1,2,3,4]}],
  "on_conflict":"defer"}` — una run in scadenza durante il deploy notturno viene saltata (`skip`,
  avanza al colpo successivo) o rinviata (`defer`, riprova finché la finestra non si libera). Un
  `end <= start` scavalca la mezzanotte.
- **Monitor SLA (17.2):** `sla = {"max_duration_s":120, "missed_grace_s":900, "channels":["inapp"]}`.
  Ricevi un avviso una tantum quando una run supera `max_duration_s`, o quando una pianificazione
  attiva è in ritardo oltre `missed_grace_s` (la run non è mai partita — il punto cieco del
  trigger `error`).
- **Navigator (17.3):** `folder`, `tags` e `archived` sui workflow.
  `GET /search?q=slack&tag=billing&folder=finance&include_archived=false` fa ricerca full-text su
  nome, descrizione **e contenuto dei nodi**; `GET /folders` elenca l'albero delle cartelle.
- **Confronto run (17.4):** `GET /runs/compare?a=<run>&b=<run>` — stato/durata/output per nodo di
  due run e il **primo nodo divergente** ("perché ieri funzionava?").
- **Digest notifiche (17.5):** `notify = {"digest": {"enabled":true, "interval_s":86400,
  "channel":"inapp"}}` — un riepilogo giornaliero (conteggi per esito) invece di un messaggio per
  run; gli avvisi `error`/`waiting` restano immediati.

**Esempio:** il template curato **Nightly report with blackout & digest** fornisce il grafo;
applica le impostazioni sopra per completarlo.

## API

Tutto ciò che fa la UI è disponibile sotto `/v1/graph-workflows` (protetto da JWT), quindi un
grafo può essere creato ed eseguito interamente da JSON senza interfaccia. Vedi la
[guida per sviluppatori](../developer-guide.md) per il riferimento completo degli endpoint.

Impostazioni: `GRAPH_WORKFLOW_SCHEDULER_ENABLED` (attivo di default) abilita il loop di polling
degli schedule; `GRAPH_WORKFLOW_MAX_NODES` limita la dimensione di un singolo grafo;
`GRAPH_WORKFLOW_FILES_DIR` è la radice dello storage di workspace per i nodi `file.*` /
`db.query` sqlite (fase 4.2); `GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT` limita l'attesa di un
nodo `human.approval`/`human.input`/`wait.event` (fase 4.4/10, default 7 giorni). Fase 12:
`GRAPH_WORKFLOW_BUDGET_WARN_PCT` (default 0.8) è la frazione di utilizzo che genera la
notifica di avviso budget; `GRAPH_WORKFLOW_RUNS_RETENTION_DAYS` (default 0 = conserva per
sempre) è il default d'istanza di retention che l'impostazione del singolo workflow può
sovrascrivere.

## Fase 19 — Custom Node SDK

Estendi la palette da solo. Un **nodo personalizzato** è un pacchetto con un
**manifest** `node.json` (`type` — sempre `custom.<name>`, `name`, `category`, schemi
JSON di `params`/`outputs`, `handles`, `secrets`, `permissions`, `kind`) in due varianti:

- **declarative** — nessun codice: un template `http.request` parametrico con
  segnaposto `{{param.x}}` / `{{input}}`. Sicuro per costruzione; retry, rate-limit e
  pin si applicano come per un connettore curato.
- **python** — un modulo che definisce `run(params, input, ctx)`, eseguito **sempre**
  nel subprocess sandbox (niente rete, limiti CPU/memoria/tempo). `ctx` espone solo i
  secret dichiarati (`ctx.secrets`) e `ctx.log` — mai il vault.

I pacchetti caricati sono versionati (la versione più alta è quella corrente); un nodo
abilitato appare nella palette con badge *custom*. L'eliminazione di un tipo è
bloccata finché un workflow lo usa. È possibile richiedere la **firma** HMAC per
istanza. Autoraggio da CLI: `sibyl-wf node init|test|pack|push`.

```
GET/POST /v1/graph-workflows/custom-nodes            (elenco / installa)
GET      /v1/graph-workflows/custom-nodes/{type}     (dettaglio, con codice)
GET/POST /v1/graph-workflows/custom-nodes/{type}/versions
PATCH    /v1/graph-workflows/custom-nodes/{type}     ({ enabled })
DELETE   /v1/graph-workflows/custom-nodes/{type}     (409 + dipendenti se in uso)
```

Impostazioni: `GRAPH_WORKFLOW_CUSTOM_NODES_DIR`, `GRAPH_WORKFLOW_REQUIRE_SIGNED_NODES`,
`GRAPH_WORKFLOW_NODE_SIGNING_KEY`.

## Fase 20 — Telegram come canale di workflow

Telegram diventa un canale **bidirezionale**, non solo un recapito di notifiche:

- **Trigger `telegram` + launcher `/run`** — associa un comando del bot (`/report`) a
  un workflow, oppure avvia qualsiasi workflow attivo dalla chat con `/run`. `$trigger =
  {chat_id, thread_id, user, text, command, args, launched_via, file?}`; l'output
  terminale `chat.reply`/`telegram.*` torna alla chat.
- **`telegram.send` / `sendMedia` / `editMessage` / `deleteMessage`** — verso qualsiasi
  chat (`chat_id` predefinito a `$trigger.chat_id`). Fuori da Telegram sono no-op puliti.
- **`telegram.ask`** — mostra pulsanti inline, sospende la run (riusa la correlazione
  `wait.event`), riprende con il valore scelto su `main` (timeout → `timeout`).
- **Media in ingresso** — un documento/foto su un trigger `telegram` viene scaricato
  nello storage del workspace ed esposto su `$trigger.file` per `file.*` / `doc.convert`
  / `kb.search` (limite `GRAPH_WORKFLOW_TELEGRAM_MAX_FILE_MB`).
- **Binding del bot** — `GET/POST/DELETE /v1/graph-workflows/telegram-bindings`
  (collisioni di comando per profilo respinte); i comandi associati sono pubblicati via
  `setMyCommands` all'avvio.
