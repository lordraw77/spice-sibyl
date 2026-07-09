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
> quattro [grafi di esempio](../examples/graph-workflows.md) già pronti — si apre sul canvas
> pronto da modificare ed eseguire.

## Il canvas

L'editor ha tre pannelli:

- **Sinistra** — i tuoi workflow e una **palette di nodi** categorizzata (Trigger · Azioni
  · Logica · Dati · IA). Ogni tool built-in, MCP e custom appare automaticamente come nodo
  `tool.<nome>`, senza scrivere codice per ogni tool.
- **Centro** — un **canvas SVG** senza dipendenze. Trascina i nodi per posizionarli;
  trascina da un **handle di output** (a destra) all'**handle di input** (a sinistra) di un
  altro nodo per collegarli. Clicca su un arco per eliminarlo.
- **Destra** — l'**ispettore** del nodo selezionato (i suoi parametri, generati dallo schema
  del tipo di nodo) oppure, quando non è selezionato nulla, il **pannello esecuzione e trigger**.

Salva con **Salva**, attiva **Attivo** per far scattare i trigger e **Esegui ora** per
lanciare subito il grafo — i nodi si colorano di verde/blu/rosso/grigio (ok/in esecuzione/
errore/saltato) in tempo reale mentre il motore trasmette lo stato via SSE.

## Tipi di nodo

| Categoria | Nodi |
|-----------|------|
| **Trigger** | `manual`, `schedule`, `webhook`, `event` |
| **Azione** | `tool.<nome>` — qualsiasi tool **integrato** (RSS, read_url, meteo, kb_search, http_request, python_exec…) |
| **MCP e custom** | ogni **tool MCP scoperto** (`tool.mcp__<server>__<tool>`) e i **tool HTTP custom** del profilo (`tool.custom__<nome>`) compaiono come nodi trascinabili — nessun codice per tool |
| **Logica** | `if` (ramo vero/falso), `switch` (rami per caso), `merge` (raccoglie gli input), `for` (for-each su un array), `repeat` (N volte) |
| **Dati** | `set` (costruisce un oggetto), `filter` (tiene gli elementi che soddisfano la condizione), `code` (sandbox Python) |
| **IA** | `llm.completion` (una chiamata al provider), `llm.agent` (l'intero loop ad agente della Fase 18, con accesso a tool integrati + MCP + custom) |

> **MCP nei flussi** — la palette è scoperta per profilo: qualsiasi server MCP configurato su
> `/mcp` e qualsiasi tool custom da `/tools` appare nel gruppo **MCP e custom** e viene eseguito
> nativamente (l'executor `tool.<nome>` instrada i nomi `mcp__*` / `custom__*`). Anche il nodo
> `llm.agent` riceve l'intero set di tool, quindi un nodo autonomo può usare MCP e tool custom.

> **Selezione del modello** — `llm.completion` e `llm.agent` mostrano un **selettore di modello
> con lo stesso catalogo e gli stessi filtri della pagina chat** (filtri provider / capacità /
> solo gratuiti, ricerca per nome e i modelli nascosti su `/providers`), così scegli il modello
> qui esattamente come nella chat. Si espande in linea nell'inspector (non un popup fluttuante).

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

Tutto ciò che non inizia con `=` è un letterale.

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

## API

Tutto ciò che fa la UI è disponibile sotto `/v1/graph-workflows` (protetto da JWT), quindi un
grafo può essere creato ed eseguito interamente da JSON senza interfaccia. Vedi la
[guida per sviluppatori](../developer-guide.md) per il riferimento completo degli endpoint.

Impostazioni: `GRAPH_WORKFLOW_SCHEDULER_ENABLED` (attivo di default) abilita il loop di polling
degli schedule; `GRAPH_WORKFLOW_MAX_NODES` limita la dimensione di un singolo grafo.
