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
> sei [grafi di esempio](../examples/graph-workflows.md) già pronti — si apre sul canvas
> pronto da modificare ed eseguire.

## Il canvas

L'editor ha tre pannelli:

- **Sinistra** — i tuoi workflow e una **palette di nodi** categorizzata (Trigger · Azioni
  · Logica · Dati · IA). Ogni tool built-in, MCP e custom appare automaticamente come nodo
  `tool.<nome>`, senza scrivere codice per ogni tool.
- **Centro** — un **canvas SVG** senza dipendenze. Trascina i nodi per posizionarli;
  trascina da un **handle di output** (a destra) all'**handle di input** (a sinistra) di un
  altro nodo per collegarli. **Clicca su un collegamento** per ispezionarlo: il pannello di
  destra mostra sorgente → destinazione, i **dati transitati nell'ultima esecuzione** e
  l'elenco appiattito dei **campi disponibili con il percorso espressione già pronto**
  (es. `$node.weather.output.result`) — un clic sul campo lo copia come espressione
  `{{ … }}`. Un pulsante elimina il collegamento. Quando un nodo fallisce, il suo **messaggio
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

## Tipi di nodo

| Categoria | Nodi |
|-----------|------|
| **Trigger** | `manual`, `schedule`, `webhook`, `event` |
| **Azione** | `tool.<nome>` — qualsiasi tool **integrato** (RSS, read_url, meteo, kb_search, http_request, python_exec…) · `http.request` (chiamata HTTP generica) · `subworkflow` (esegue un altro workflow inline) |
| **MCP e custom** | ogni **tool MCP scoperto** (`tool.mcp__<server>__<tool>`) e i **tool HTTP custom** del profilo (`tool.custom__<nome>`) compaiono come nodi trascinabili — nessun codice per tool |
| **Logica** | `if` (ramo vero/falso), `switch` (rami per caso), `merge` (raccoglie gli input), `for` (for-each su un array), `repeat` (N volte) |
| **Dati** | `set` (costruisce un oggetto), `filter` (tiene gli elementi che soddisfano la condizione), `code` (sandbox Python) |
| **Notifiche** | `notify.telegram` (chat Telegram collegata), `notify.email` (SMTP), `notify.webhook` (Slack/Discord/ntfy/webhook qualsiasi), `notify.inapp` (campanella della web UI, zero configurazione) |
| **IA** | `llm.completion` (una chiamata al provider), `llm.agent` (l'intero loop ad agente della Fase 18, con accesso a tool integrati + MCP + custom) |

> **MCP nei flussi** — la palette è scoperta per profilo: qualsiasi server MCP configurato su
> `/mcp` e qualsiasi tool custom da `/tools` appare nel gruppo **MCP e custom** e viene eseguito
> nativamente (l'executor `tool.<nome>` instrada i nomi `mcp__*` / `custom__*`). Anche il nodo
> `llm.agent` riceve l'intero set di tool, quindi un nodo autonomo può usare MCP e tool custom.

> **Selezione del modello** — `llm.completion` e `llm.agent` mostrano un **selettore di modello
> con lo stesso catalogo e gli stessi filtri della pagina chat** (filtri provider / capacità /
> solo gratuiti, ricerca per nome e i modelli nascosti su `/providers`), così scegli il modello
> qui esattamente come nella chat. Si espande in linea nell'inspector (non un popup fluttuante).

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

### Notifiche — `notify.*`

Quattro nodi terminali consegnano il risultato di un workflow su un canale; combinali
con il ramo di errore per flussi "avvisami quando si rompe":

- **`notify.telegram`** — invia `text` alla **chat Telegram collegata al profilo**
  (Impostazioni → Telegram, lo stesso ponte delle notifiche dei promemoria). Fallisce se
  nessuna chat è collegata; una chat silenziata (`/notify off`) è un no-op silenzioso.
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
  tra un tentativo e l'altro.
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
  per elemento**, con `$item` e `$index` disponibili in quell'iterazione.
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
  `$env` (variabili d'ambiente con prefisso WF_), `$now`. Funzioni: `default`, `upper`,
  `lower`, `trim`, `len`, `join`, `slice`, `first`, `last`, `get`, `keys`, `values`, `round`, …

- `=py: …` — una **via di fuga** verso la sandbox `python_exec` per logica reale
  (comprehension di liste, ecc.). Sono disponibili `ctx`, `input`, `node`, `trigger`;
  l'ultima espressione (o una variabile `result`) diventa il valore.

Tutto ciò che non inizia con `=` è un letterale — con un'eccezione tollerante: un
`{{ … }}` nudo (senza il `=` iniziale) è un errore così comune che viene risolto
esattamente come `={{ … }}`.

> **I nodi non collegati non partono** — solo i nodi *trigger* sono punti di ingresso.
> Un nodo trascinato sul canvas ma non collegato al flusso viene registrato come
> `skipped` all'esecuzione invece di partire da solo.

## Trigger

Dal pannello di esecuzione puoi collegare:

- **Schedule** — cron / RRULE / linguaggio naturale ("ogni giorno alle 9:00"), interpretato
  dallo stesso motore dei promemoria. Un loop di polling in background esegue gli schedule
  scaduti e ricalcola il prossimo orario. (Scatta solo quando il workflow è **Attivo**.)
- **Webhook** — un URL pubblico con token (`POST /api/v1/wf/hooks/{token}`). Il corpo JSON
  diventa `$trigger`. Scatta solo quando il workflow è Attivo.
- **Event** — eventi interni (es. documento ingerito, promemoria scattato).

## Versioni ed esecuzioni

Ogni salvataggio crea uno snapshot di versione immutabile; puoi elencare le versioni e
tornare indietro. Ogni esecuzione salva il grafo eseguito, il contesto risolto e un record
per nodo (input, output, errore, tempi) ispezionabile a posteriori.

Poiché ogni valore viene persistito, l'editor non ha bisogno di una run live per mostrare
i dati: all'apertura di un workflow carica **l'ultimo output registrato di ogni nodo su
tutte le esecuzioni passate** (`GET /{id}/node-outputs`), quindi cliccando una freccia
vedi i campi e il payload transitati storicamente — con la nota "dati da un'esecuzione
passata" e il relativo orario. Una nuova run sostituisce quei valori con quelli live.

**Export**: il pulsante *Esporta* (o `GET /{id}/export`) scarica il workflow come
snapshot JSON portabile (`{ kind, schema_version, name, description, graph, … }`); lo
stesso corpo è re-importabile via `POST /v1/graph-workflows`.

**Import**: il pulsante 📥 accanto a **Nuovo** (in cima all'elenco dei workflow) apre un
file `.workflow.json` dal disco — esattamente il file prodotto da **Esporta** — e crea un
nuovo workflow da esso, aprendolo subito per la modifica. Legge solo `name`,
`description` e `graph`; i campi presenti solo nell'export (`kind`, `schema_version`,
`exported_at`, …) vengono accettati e ignorati, quindi un file scaricato in precedenza con
Esporta (da questa istanza o da un'altra) si reimporta senza problemi. Un file JSON non
valido o che non è un workflow viene rifiutato lato client con un messaggio di errore,
senza essere inviato al server.

## API

Tutto ciò che fa la UI è disponibile sotto `/v1/graph-workflows` (protetto da JWT), quindi un
grafo può essere creato ed eseguito interamente da JSON senza interfaccia. Vedi la
[guida per sviluppatori](../developer-guide.md) per il riferimento completo degli endpoint.

Impostazioni: `GRAPH_WORKFLOW_SCHEDULER_ENABLED` (attivo di default) abilita il loop di polling
degli schedule; `GRAPH_WORKFLOW_MAX_NODES` limita la dimensione di un singolo grafo.
