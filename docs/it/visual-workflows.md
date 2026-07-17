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
> quindici [grafi di esempio](../examples/graph-workflows.md) già pronti — coprono ogni
> tipo di nodo (logica, loop, dati, notifiche, AI) — si apre sul canvas
> pronto da modificare ed eseguire.


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
| **Trigger** | `manual`, `schedule`, `webhook`, `event` |
| **Azione** | `tool.<nome>` — qualsiasi tool **integrato** (RSS, read_url, meteo, kb_search, http_request, python_exec…) · `http.request` (chiamata HTTP generica) · `subworkflow` (esegue un altro workflow inline) · `human.approval` (sospende finché un umano approva/rifiuta — fase 4.4) |
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
ricreare nell'ambiente di destinazione.

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

## API

Tutto ciò che fa la UI è disponibile sotto `/v1/graph-workflows` (protetto da JWT), quindi un
grafo può essere creato ed eseguito interamente da JSON senza interfaccia. Vedi la
[guida per sviluppatori](../developer-guide.md) per il riferimento completo degli endpoint.

Impostazioni: `GRAPH_WORKFLOW_SCHEDULER_ENABLED` (attivo di default) abilita il loop di polling
degli schedule; `GRAPH_WORKFLOW_MAX_NODES` limita la dimensione di un singolo grafo;
`GRAPH_WORKFLOW_FILES_DIR` è la radice dello storage di workspace per i nodi `file.*` /
`db.query` sqlite (fase 4.2); `GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT` limita l'attesa di un
nodo `human.approval` (fase 4.4, default 7 giorni).
