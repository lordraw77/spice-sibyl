# Visual node-graph workflows (Phase 29)

SpiceSibyl has two complementary automation engines:

- **Agent workflows** (`/workflows`, Phase 18) — you give a *goal* and an LLM iterates
  over the full tool registry autonomously until it produces an answer. Powerful, but
  non-deterministic and with no explicit control flow.
- **Visual workflows** (`/graph-workflows`, Phase 29) — you draw a *graph*: a **trigger**
  feeds **typed nodes** wired together with connections. The engine runs the graph
  **deterministically**, in the exact shape you designed. The agent loop is still
  available here as the `llm.agent` node, so you can drop autonomy into a deterministic
  pipeline where you want it.

![Visual workflow editor](screenshots/visual-workflow-editor.svg)

> **In a hurry?** Click ✨ on the `/graph-workflows` page and **Import** one of the six
> ready-made [example graphs](../examples/graph-workflows.md) — it opens on the canvas
> ready to edit and run.


![Visual editor — componentized canvas, palette and run panel](../screenshots/editor-overview.png)

<p align="center">
  <img src="../screenshots/run-panel-vars-secrets-versions.png" alt="Run panel: $vars editor, $secrets manager, version history" width="360" />
</p>

![Per-workflow shell — Editor | Runs | Schedules tabs with the run detail open](../screenshots/workflow-shell-runs.png)

## The canvas

The editor is a three-pane layout:

- **Left** — your workflows and a categorised **node palette** (Triggers · Actions ·
  Logic · Data · AI). Every built-in, MCP and custom tool automatically appears as a
  `tool.<name>` action node — no new code per tool.
- A small toolbar above the canvas gives **Undo/Redo** (`Ctrl+Z` / `Ctrl+Shift+Z`, also
  `Ctrl+Y` for redo), **Copy/Paste** a node (`Ctrl+C` / `Ctrl+V` — pastes an offset
  duplicate with the same type and params), and **Comment**: a frontend-only sticky-note
  node for annotating the canvas. Comments have no input/output handles and are never
  wired into the flow, so the engine simply records them as `skipped` like any other
  unconnected node — no backend changes needed. Keyboard shortcuts are ignored while
  typing in a field. A **search box** above the node palette filters it by label or type
  (also auto-expanding matching MCP/custom tool groups while searching).
- **Center** — a dependency-free **SVG canvas**. Drag nodes to lay them out; drag from a
  node's **output handle** (right) to another node's **input handle** (left) to connect
  them. **Click an edge** to inspect it: the right panel shows source → target, the
  **data that flowed through it on the last run**, and a flattened list of **available
  fields with their ready-made expression paths** (e.g. `$node.weather.output.result`) —
  click a field to copy it as a `{{ … }}` expression. A delete button removes the
  connection.
  **Connect-time auto-mapping**: the moment you draw a connection, the editor pre-fills
  the target node's first empty expression-capable parameter with the source node's
  output. It inspects the source's latest recorded output (live or from run history) to
  understand its shape — text, number, list (with length), object (with its keys). When
  the source provides a single unambiguous value and the target has one empty field, the
  mapping is applied silently (a toast confirms it). Otherwise a **chooser dialog** opens:
  it lists each candidate value with its expression path, its **type** and a **preview**
  so you can tell them apart, lets you pick which target field to fill when several are
  empty, and offers *Not now* to skip. Fields the user already filled are never
  overwritten. The mapping is **loop-aware**: connecting from a for/repeat node's `loop`
  handle offers `$item` / `$index` (the per-iteration scope — `$node.<loopId>.output`
  does not exist inside the body), while its `done` handle offers `…output.items`; and
  an `items` target param (for/filter/aggregate/batch) preselects the first list-shaped
  value, e.g. a tool node's parsed `.json` rather than its raw `.result` text.
  When a node fails, its **error message** appears in red under the node in the live run
  panel (and in the Runs view detail).
- **Right** — the **inspector** for the selected node (its parameters, rendered from the
  node type's schema), or, when nothing is selected, the **run & triggers panel**.

Save with **Save**, flip **Active** to let triggers fire, and **Run now** to execute the
graph immediately — nodes light up green/blue/red/grey (ok/running/error/skipped) live as
the engine streams progress over SSE. The run panel has an optional **Run payload** JSON
box: its object becomes `$trigger` for the run, so graphs that read
`={{ $trigger.<field> }}` (like the webhook and subworkflow examples) can be exercised
manually without a webhook call.

### The per-workflow view — `/graph-workflows/{id}`

Every workflow also has its own page (open it with the ⧉ button in the workflow list, or
from a run/schedule row): an **Editor | Runs | Schedules** tab bar scoped to that
workflow. The Runs tab is the execution registry pre-filtered to this workflow (the
launch control targets it directly); the Schedules tab lists and creates triggers for it
only. The global pages (`/graph-workflows`, `/graph-workflows/runs`,
`/graph-workflows/schedules`) remain the cross-workflow views.

The editor itself is componentized (roadmap fase 1): the SVG canvas, the node palette,
the edit toolbar, the node/edge inspectors and the run panel are standalone Angular
components under `features/workflows/editor/`, orchestrated by a thin page component —
see `docs/frontend-overview.md`.

### Editor DX — test, pin, navigate (fase 3)

Building and debugging a graph doesn't require full runs:

- **Test node** (⚡ in the inspector) executes **only the selected node**, with its
  current — even unsaved — parameters, and shows output, active handle and duration
  inline (`POST /{id}/nodes/{node_id}/test`; nothing is recorded in the run registry).
  Its input comes from the upstream node's pinned/latest output, or from the optional
  **mock input** JSON in the inspector.
- **Pinned outputs** (📌) freeze a node's output — one click on its latest output, or
  hand-edited JSON. Node tests, **partial runs** (*Run from this node*) and expression
  previews resolve `$node.<id>.output` from the pin instead of run history: ideal for
  developing downstream of a real webhook payload without re-firing it. Pins are saved
  with the workflow (and travel with export), show a 📌 badge on the canvas, and are
  **completely ignored by production runs** (manual/schedule/webhook/event).
- **Last execution** in the inspector shows the selected node's latest status, output
  and error (live run, node test or history) without leaving the canvas.
- **Multi-selection**: shift+click adds/removes nodes; dragging moves the whole
  selection; `Ctrl+A` selects all; `Ctrl+C/V` copy & paste the selection **including
  its internal edges** (ids remapped); `Delete`/`Backspace` removes it.
- **Pan & zoom**: drag the empty canvas to pan, mouse wheel to zoom around the cursor.
  A **minimap** (bottom right) shows the whole graph plus the viewport — click/drag it
  to navigate, double-click to fit. The toolbar adds **Arrange** (layered auto-layout,
  undoable like any edit) and **⛶ fit view**.
- The **template gallery** (✨) opens as a **large centered modal** over the editor:
  a multi-column grid of cards, each with a bigger graph preview, the category, the
  flow chain (node names joined by →), node/connection counts and the full description —
  filterable by category before importing. The **workflow list is collapsible** (▾/▸ in
  its header, remembered across sessions) so the node palette gets the sidebar's space
  while editing.

## Node types

| Category | Nodes |
|----------|-------|
| **Trigger** | `manual`, `schedule`, `webhook`, `event` |
| **Action** | `tool.<name>` — any **built-in** tool (RSS, read_url, weather, kb_search, http_request, python_exec…) · `http.request` (generic HTTP call) · `subworkflow` (run another workflow inline) · `human.approval` (suspend until a human approves/rejects — fase 4.4) |
| **MCP & custom** | every **discovered MCP server tool** (`tool.mcp__<server>__<tool>`) and the profile's **custom HTTP tools** (`tool.custom__<name>`) appear as drag-in nodes — no code per tool |
| **Logic** | `if` (true/false branch), `switch` (case branches), `merge` (collect inputs), `for` (for-each over an array), `repeat` (N times), `wait` (pause for N seconds or until a point in time) |
| **Data** | `set` (build an object), `filter` (keep matching array items), `code` (Python sandbox), `aggregate` (reduce an array — sum/avg/min/max/count/concat over a field), `batch` (split an array into fixed-size chunks), `db.query` (parameterised SQL — sqlite/postgres), `file.read` / `file.write` (workspace storage), `file.parse` (parse in-flight JSON/CSV/lines) |
| **Notify** | `notify.telegram` (linked Telegram chat), `notify.email` (SMTP), `notify.webhook` (Slack/Discord/ntfy/any webhook), `notify.inapp` (web UI bell, zero config) |
| **AI** | `llm.completion` (one provider call), `llm.agent` (the full Phase 18 agent loop, with access to built-in + MCP + custom tools), `llm.classify` / `llm.extract` (guaranteed-structured output — fase 4.1) |

> **MCP in flows** — the palette is discovered per profile, so any MCP server configured
> on `/mcp` and any custom tool from `/tools` shows up in the **MCP & custom** group and
> runs natively (the `tool.<name>` executor routes `mcp__*` / `custom__*` names). The
> `llm.agent` node is also handed the full tool set, so an autonomous node can call MCP
> and custom tools too.

> **Model selection** — `llm.completion` and `llm.agent` expose a **model picker with the
> same catalog and filters as the chat page** (provider / capability / free-only filters,
> name search, and the models you hid on `/providers`), so you pick a model here exactly
> as you do in chat. It expands inline in the inspector (not a floating popup).

> **Failover chains** — both nodes also expose a **Failover chain** dropdown, populated
> from named model lists curated in Settings → Models → LLM failover chains (admin-only to
> edit, visible to everyone in the picker). When set, a call failure on the node's `model`
> retries — in order — through the chain's remaining models until one succeeds or all are
> exhausted; the node output then carries `_failover: { tried: [...], used: "<model>" }`. For
> `llm.agent`, a successful fallback is sticky: later agent-loop steps start from whichever
> model just worked, instead of retrying the original one every time.

### HTTP requests — `http.request`

A first-class node for calling **any external HTTP API** (no tool definition needed).
Parameters: `method`, `url`, `query` / `headers` (JSON objects), `body` (a JSON value is
sent as JSON, anything else as raw text), `timeout` (seconds, capped at 120). The output
is `{ status, ok, headers, json, text }` — `json` is parsed when the response is JSON, so
downstream nodes can read `={{ $node.api.output.json.<field> }}`.

By default a **non-2xx response raises**, which means the node's retry and *On error*
policies apply (see below) — ideal for "retry twice, then alert" patterns. Set
`allow_errors` to a truthy value to receive the response unchanged regardless of status.

### Composition — `subworkflow`

Runs **another workflow of the same profile inline** as a child run and returns when it
finishes. Parameters: `workflow_id` and an optional `payload` (JSON object) that becomes
the child's `$trigger`; without a payload, this node's input is passed as
`{ input: … }`. The output is `{ run_id, workflow_id, status, output }`, where `output`
is the child's **sink node output** (or a map of them when the child has several sinks).
The child executes as a normal, fully observable run (`trigger_type: subworkflow`) with
its own node records and SSE stream. Nesting is capped at **5 levels** and self-recursion
fails the run rather than looping forever.

### Structured AI — `llm.classify` / `llm.extract` (fase 4.1)

Two AI nodes whose output shape is **guaranteed**, replacing the fragile "free prompt +
JSON parsing in a `code` node" pattern:

- **`llm.classify`** — classifies `input` (an expression; defaults to the node input)
  into one of the declared `categories` (JSON array or comma-separated list). The model
  must reply `{category, confidence}` with a category **from the list** — anything else
  raises, so the node's retry / *On error* policies apply instead of garbage flowing
  downstream. Output: `{ category, confidence, model, _usage }`. Route the result with a
  `switch` on `={{ $node.<id>.output.category }}`.
- **`llm.extract`** — extracts structured data matching a **JSON Schema** declared in
  the inspector (`schema` param). Top-level `required` properties are enforced; a
  non-conforming reply raises. Output: `{ data, model, _usage }`.

Both expose the same model picker and **failover chain** as `llm.completion`, use the
response cache, and ship with a retry preset (1 retry, exponential backoff, 120 s timeout).
Code fences and prose around the model's JSON are tolerated and stripped.

### Database & files — `db.query`, `file.read`, `file.write`, `file.parse` (fase 4.2)

- **`db.query`** — runs parameterised SQL and outputs `{ rows, count, rowcount }` (rows
  capped at 1000). `driver: sqlite` (default) stores the database file **inside the
  workspace storage** (`database` is a relative path, e.g. `app.db`); `driver: postgres`
  connects via a `dsn` — keep it in `$secrets` (`={{ $secrets.PG_DSN }}`), never inline.
  Use `?` placeholders (sqlite) / `$1…` (postgres) with the `params` JSON array — values
  are never interpolated into the SQL string.
- **`file.read`** — reads a file from the workspace storage and parses it by `format`
  (`auto` by extension): `json → {data}`, `csv → {rows, count}`, `lines → {lines, count}`,
  `text → {text, size}`. Files are capped at 10 MB.
- **`file.write`** — writes `content` (or the node input) to the workspace storage;
  objects/arrays serialise as JSON, `format: csv` renders a list of objects with a header
  row, `append: true` appends. Output: `{ path, format, bytes_written, append }`.
- **`file.parse`** — parses an **in-flight text payload** (an `http.request` body, a tool
  result…) without touching disk, same outputs as `file.read`.

**Sandbox** — every path is resolved *inside* `GRAPH_WORKFLOW_FILES_DIR` (default
`data/workflow_files`); absolute paths and `..` traversal that would escape it fail the
node. Credentials for external databases belong in `$secrets` (fase 1.3), so they are
encrypted at rest and never exported with the workflow.

### Human-in-the-loop — `human.approval` (fase 4.4)

The run **suspends** on this node (run status `waiting`, purple chip) until a human
decides. On execution it creates an approval request, pushes an **in-app notification**
(optionally Telegram with `telegram: true`), then waits. Decide from the **Runs view** —
opening a `waiting` run shows the request with **✓ Approve / ✕ Reject** buttons and an
optional comment — or via API. The decision routes the graph through the **`approved`**
or **`rejected`** output handle with `{ approved, status, comment, decided_by }` as
output, so each branch gets its own follow-up.

Params: `title`, `message` (expression), `timeout` (seconds, default 24 h, capped by
`GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT` — default 7 days) and `onTimeout` (`reject` routes
an expired request through the rejected branch; `fail` fails the node, so retry / error
branch apply). Thanks to the fase 2.4 checkpoints the wait **survives restarts**: a
resumed run re-attaches to its pending request instead of creating a new one. Cancelling
a waiting run settles its request as `cancelled`. A `waiting` run does **not** hold a
`max_concurrent_runs` slot.

```
GET  /v1/graph-workflows/approvals                 ?status=pending&run_id=   (list)
POST /v1/graph-workflows/approvals/{aid}/decision  { approved: true|false, comment? }
```

### Notifications — `notify.*`

Four sink nodes deliver a workflow's result to a channel; combine them with the error
branch for "alert me when it breaks" flows:

- **`notify.telegram`** — sends `text` to the **Telegram chat linked to the profile**
  (Settings → Telegram, same bridge as reminder notifications). Fails when no chat is
  linked; a muted chat (`/notify off`) is a silent no-op. An optional `parse_mode`
  (`Markdown` / `MarkdownV2` / `HTML`, empty = plain text) renders formatting instead of
  showing raw markup — pick it when `text` comes from an `llm.*` node that writes
  CommonMark. `**bold**` (CommonMark) is automatically normalised to Telegram's own
  single-asterisk `*bold*` when a Markdown mode is selected, since Telegram doesn't
  recognise the double-asterisk form and would otherwise print it literally. Messages
  longer than Telegram's 4096-char limit are automatically split into multiple messages
  on line boundaries, so long digests are never dropped.
- **`notify.email`** — plain-text email (`to`, `subject`, `body`) through the SMTP
  server configured via `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` /
  `SMTP_FROM` / `SMTP_STARTTLS`. Unconfigured SMTP fails the node, so retry / On error
  apply.
- **`notify.webhook`** — POSTs a JSON `payload` (defaults to the node's input) to any
  external webhook URL — Slack/Discord incoming webhooks, ntfy, home automation, …
- **`notify.inapp`** — pushes `title`/`body` to the **web UI notification bell**
  (persisted and streamed live over SSE). Needs zero configuration — the safe default.

### The Runs view — execution registry

The designer keeps only a lightweight live panel; the durable record lives in the
**Runs** page (`/graph-workflows/runs` — its own navbar entry under Tools, gated by the
same `graph_workflows` feature flag as the designer, and also reachable via "Runs →" in
the editor header). It lists every run of the profile across all workflows — status,
trigger, start time, duration — filterable by workflow and status, auto-refreshing while
something is executing. From the same toolbar you can **start a workflow** (pick it,
optionally paste a `$trigger` JSON payload, press Run) and **stop** any pending/running
run (`POST /v1/graph-workflows/runs/{id}/cancel` — the engine cancels the task and the
run settles as `cancelled`). Selecting a run shows its per-node results (status, error,
output) and, when the run is still going, follows it **live over SSE**; "Open in
designer" jumps back to the graph.
Switching workflows in the designer no longer loses an execution: the editor re-attaches
to the latest running run when you re-open its workflow, and the Runs view is always the
source of truth (`GET /v1/graph-workflows/runs`).

### The Schedules view — cross-workflow trigger overview

`/graph-workflows/schedules` (Phase 30.e, same navbar group and feature flag) lists **one
row per trigger** across every workflow of the profile: workflow name, trigger type, next
run time (schedule triggers), last run status/time, consecutive-failure count, and an
enable/disable toggle, **Run**, and **Delete** — so you can see everything that's due, or
broken, without opening each workflow individually. "Run" launches the workflow
immediately; the workflow name links back into the designer. Backed by
`GET /v1/graph-workflows/schedules`.

> **A trigger only fires while its *workflow* is Active** — enabling/disabling a trigger
> is separate from the workflow's own Active flag (toggled on the designer, or right next
> to the workflow name here as an Active/Inactive pill). A perfectly configured, enabled
> trigger on an Inactive workflow will never fire; the **+ New trigger** form warns and
> offers a one-click Activate when the picked workflow is Inactive, since this is the most
> common reason a newly created schedule silently does nothing.

**Creating a trigger** (Phase 30.f) — the **+ New trigger** panel picks a workflow and a
trigger type (`schedule` / `webhook` / `event`); for `schedule` it exposes a structured
pattern instead of free natural language, so the picked day/time or cron expression is
honoured exactly:

- **Daily** — a single `time` (HH:MM); fires at that time every day.
- **Weekly** — one or more weekdays plus a `time`; fires on each picked weekday.
- **Cron** — a preset dropdown (every 15 min, hourly, daily at midnight, weekdays at 9:00)
  that fills in a **free-text 5-field cron expression** (`min hour dom mon dow`), which
  stays editable for any custom pattern; validated with `croniter` before saving.
- **Once** — an optional `date` (YYYY-MM-DD, defaults to the next occurrence of `time` if
  omitted) plus a `time`.

`event` triggers take a plain `event` name (`document.ingested` and `chat.message.created`
are wired today; any other name is accepted for future events). `webhook` triggers need no
extra config here — generate/rotate its signature secret from the designer after creation
(see Triggers below). All of this is `POST /{wf_id}/triggers` under the hood — the pattern
is resolved server-side into the same compact `recurrence` string described below, so API
clients can keep using the plain `text`/`recurrence` fields directly if they prefer.

### Error handling — retries and the error branch

Every node has three failure controls in the inspector's **Advanced** section:

- **Retries** / backoff — re-run the node up to N times, waiting `backoff` seconds
  between attempts. The **Backoff strategy** (fase 2.1) picks how the pause grows:
  **Fixed** always sleeps `backoff` seconds; **Exponential** sleeps
  `backoff × 2^attempt` (1st retry after `backoff`s, 2nd after `2×backoff`s, …),
  capped at 60 s per pause. New `http.request` and `llm.*` nodes come pre-configured
  with sensible presets from the palette catalog (e.g. HTTP: 2 retries, 2 s
  exponential backoff, 60 s timeout) — tune or clear them per node.
- **Timeout (ms)** — a hard wall-clock cap for a *single* execution attempt (`0`
  disables it, max 600 000). A timed-out attempt is aborted and fails like any other
  error, so it is still subject to retries/backoff and the **On error** policy below —
  the idiomatic guard for a hung `http.request`, `llm.agent` or MCP tool that would
  otherwise stall the whole run.
- **On error** — what happens once retries are exhausted:
  - **Stop the run** (default) — the run fails.
  - **Continue on main** — the node reports `{ error }` on its `main` output and the
    flow continues (the legacy `continueOnFail` flag behaves the same).
  - **Route to error branch** — the node grows a dedicated **`error` output handle**;
    on failure, `{ error, input }` flows down that branch while the `main` branch is
    skipped (and vice versa on success). This gives you try/catch shapes on the canvas:
    wire the happy path to `main` and a fallback/alert chain to `error`.

The node run is still recorded (and coloured) as an **error** when it routes to the
error branch, so run history stays truthful while the run itself completes.

### Production hardening: concurrency, usage, alerting

- **Concurrency cap** — each run wave executes independent, ready nodes in parallel; a
  `GRAPH_WORKFLOW_MAX_CONCURRENT_NODES` semaphore (default 8) bounds how many run at once
  within a single run, so a wide graph can't saturate the process.
- **Per-workflow run queue** (fase 2.3) — set **Max concurrent runs** in the run panel's
  **Execution** section (or `max_concurrent_runs` via API, `0` = unlimited): runs beyond
  the limit are created in status **`queued`** (their trigger payload parked with the run)
  and start FIFO as slots free up. A busy schedule or a webhook burst can no longer pile
  concurrent runs onto the backend. Queued runs show up in the Runs view and can be
  cancelled like any other run. Subworkflow child runs bypass the queue (a queued child
  would deadlock its waiting parent).
- **Checkpoint & resume** (fase 2.4) — the run context (every node's output **and its
  active output handles**) is persisted after each wave. At startup (gated by
  `GRAPH_WORKFLOW_RESUME_ON_STARTUP`, default true) runs left `running`/`pending` by a
  crash or restart are resumed from that checkpoint: completed nodes are not re-executed,
  their outputs keep resolving in downstream expressions, and only the remaining subgraph
  runs. Node runs orphaned mid-execution are settled as errors ("interrupted by restart")
  and re-executed by the resumed run.
- **Error trigger** (fase 2.5) — attach an **error** trigger to a workflow (run panel →
  “＋ error”) and it fires whenever *another* workflow's run fails, receiving
  `{workflow_id, workflow_name, run_id, error, failed_node}` as `$trigger`. Watch a single
  workflow by setting `config.workflow_id`, or leave it empty (`""`/`*`) to react to every
  failure. Combine it with the `notify.*` nodes for centralised alerting. Loop-guarded:
  a workflow never reacts to its own failures, and runs started by an error trigger never
  cascade further error triggers.
- **Token usage** — `llm.completion` and `llm.agent` node outputs include a `_usage` key
  (`{tokens_in, tokens_out, tokens_total}`, summed across agent-loop steps) whenever the
  provider reports it; `null` when it doesn't. No per-model cost table exists yet, so cost
  is intentionally not estimated.
- **Recurring-failure alert** — after `GRAPH_WORKFLOW_RUN_FAILURE_ALERT_THRESHOLD` (default
  3) consecutive failed runs of the same workflow, an in-app notification fires once (not
  on every subsequent failure) so a broken workflow doesn't fail silently forever.
- **Response cache** — `llm.completion` and each `llm.agent` step reuse the same response
  cache as chat (`RESPONSE_CACHE_ENABLED`, `RESPONSE_CACHE_TTL_SECONDS`,
  `RESPONSE_CACHE_MAX_ENTRIES`, plus the Phase 26 `SEMANTIC_CACHE_*` fuzzy layer). An
  identical `(model, messages, temperature, max_tokens)` skips the provider entirely; node
  output carries `_cache: "hit" | "semantic" | "miss"` alongside `_usage` for observability.
  Tool-calling `llm.agent` steps are never cached (same rule as chat: a request with
  `tools` never gets a cache key).

### Waiting, aggregating and batching — `wait`, `aggregate`, `batch`

- **`wait`** — suspends the node before continuing. Set `seconds` for a fixed delay, or
  `until` (a unix timestamp or an ISO-8601 datetime, usually an expression) to sleep until
  that point in time. Either way the delay is capped at one hour so a mistyped date can't
  hang a run. Output: `{ waited }` (seconds actually slept).
- **`aggregate`** — reduces an array (`items`, or the node's input) into a single value with
  `op` (`sum`/`avg`/`min`/`max`/`count`/`concat`) applied over a dotted `field` path (e.g.
  `price` or `order.total`). Output: `{ result, count }`.
- **`batch`** — splits an array (`items`, or the node's input) into chunks of `size`, output
  `{ batches, count }` — feed it into a `for` node (`items: ={{ $node.b.output.batches }}`)
  to process an array N items at a time instead of one by one.

### Loops — `for` and `repeat`

`for` and `repeat` have two outputs: **`loop`** (the body) and **`done`** (the continuation).
Wire the body chain to the `loop` output and the rest of the flow to `done`:

- **`for`** takes an array (`items`, e.g. `={{ $trigger.urls }}`) and runs the body **once per
  item**, with `$item` and `$index` in scope for that iteration. Body nodes that already ran
  **in the same iteration** are also addressable as `$node.<id>.output` (so the edge
  inspector's field paths work inside the body too); each iteration sees only its own values.
- **`repeat`** runs the body a fixed number of `times`, with `$index` in scope.

Each iteration's body result is collected; when the loop finishes it outputs
`{ items: [...], count }` on `done`, so the continuation can read
`={{ $node.<loopId>.output.items }}`. The body is the subgraph reachable from `loop`
(and not from `done`); keep it a linear chain. Iterations are capped for safety.

## Expressions

Any parameter can be a literal **or** an expression. Two forms are dispatched by prefix:

- `={{ … }}` — a **safe mini-expression**. It is parsed and walked over a whitelist
  (**no `eval`/`exec`**), so it is safe to expose in the UI. You can navigate the run
  context and call a fixed set of pure functions:

  ```
  ={{ $node.rss.output.result }}          # another node's output
  ={{ $trigger.count }}                    # the trigger payload
  ={{ upper($json.title) }}                # whitelisted function
  ={{ default($trigger.name, 'world') }}
  ={{ $trigger.count > 3 }}                # comparisons → if/switch
  Hi ={{ $trigger.name }}!                 # string interpolation
  ```

  Context: `$node.<id>.output.<path>`, `$json` (this node's primary input), `$trigger`,
  `$env` (WF_*-prefixed env vars), `$vars` (the workflow's own variables), `$secrets`
  (profile secrets, decrypted only for the duration of a run), `$now`. Functions include
  `default`, `upper`, `lower`, `trim`, `len`, `join`, `slice`, `first`, `last`, `get`,
  `keys`, `values`, `round`, …

- `=py: …` — an **escape hatch** into the `python_exec` sandbox for real logic
  (list comprehensions, etc.). `ctx`, `input`, `node`, `trigger` are available; the last
  expression (or a `result` variable) becomes the value.

Anything not starting with `=` is a plain literal — with one forgiving exception: a bare
`{{ … }}` (without the leading `=`) is such a common slip that it resolves exactly like
`={{ … }}`.

A **lone** expression keeps its native type (list, dict, number…); surrounding it with
text stitches the result into a string. Whitespace and newlines around the expression
don't count: `{{ … }}` followed by an accidental Enter in the textarea stays native —
which matters for For-each/Filter's `items` param, which needs a real list.

> **Unwired nodes don't run** — only *trigger* nodes are entry points. A node dropped on
> the canvas but not connected to the flow is recorded as `skipped` at run time instead
> of firing on its own.

## Variables & secrets — `$vars` / `$secrets`

Two configuration scopes keep values out of node params (roadmap fase 1):

- **Variables (`$vars`)** — per-workflow key/value pairs, edited in the run panel's
  *Variables* section and readable from any node as `{{ $vars.name }}`. A value that
  parses as JSON keeps its native type (list, object, number, boolean). Variables travel
  with **Export/Import** and with the API (`variables` on `POST` / `PATCH`); changing them
  does **not** bump the graph version.
- **Secrets (`$secrets`)** — profile-scoped credentials shared by all your workflows
  (API tokens, connection strings…), managed in the run panel's *Secrets* section.
  Values are **Fernet-encrypted at rest** (derived from `VAULT_SECRET_KEY`, the same
  master secret as the provider key vault) and are **never returned by the API** — the
  list shows names only. Reference one as `{{ $secrets.NAME }}` (e.g. in an
  `http.request` header). The engine decrypts them only for the duration of a run; the
  persisted run context never contains them, the editor's *Test expression* resolves
  them as `***`, and Export deliberately omits them — re-create them in the target
  environment.

## Triggers

Attach triggers from the run panel:

- **Schedule** — cron / RRULE / natural language ("every day at 9:00"), parsed by the
  same engine as reminders. A background poll loop fires due schedules and recomputes the
  next run time. (Only fires while the workflow is **Active**.)
- **Webhook** — a public, token-scoped URL (`POST /api/v1/wf/hooks/{token}`). The JSON
  body becomes `$trigger`. Only fires while the workflow is Active. Optionally protect it
  with a shared secret: `POST /v1/graph-workflows/triggers/{tid}/rotate-secret` generates
  one (returned once — copy it into whatever sends the webhook) and, from then on, the
  request must carry `X-Signature: sha256=<hex hmac-sha256 of the raw body>` or it is
  rejected with 401 before the body is parsed or the workflow runs.
- **Event** — internal events. Set the trigger's `config.event` to the event name (or
  leave it empty / `*` to match any event). Two events are wired today:
  `document.ingested` (fires after a KB document/URL finishes ingesting — payload
  `{doc_id, filename, profile_id}`) and `chat.message.created` (fires after a chat
  exchange is persisted — payload `{conversation_id, profile_id}`).
- **Error** (fase 2.5) — fires when another workflow's run fails. `config.workflow_id`
  narrows it to one watched workflow (empty / `*` = any). The payload is
  `{workflow_id, workflow_name, run_id, error, failed_node}`; pair the trigger with an
  `error` trigger *node* on the canvas as the entry point. Self-failures and failures of
  error-triggered runs never fire it (loop guard).

Both **schedule** and **event** triggers track a consecutive-failure streak
(`fail_count`/`last_error` on the trigger): after
`GRAPH_WORKFLOW_TRIGGER_MAX_FAILURES` (default 5) failures in a row the trigger
auto-disables and an in-app notification is raised so a broken trigger doesn't fail
silently forever. Re-enabling a trigger (`POST /triggers/{tid}/enable`) clears the streak.

## Versioning & runs

Every save snapshots an immutable version; you can list versions and roll back. The run
panel's **Versions** section lists every snapshot with its timestamp and offers a
one-click **Restore** — restoring itself snapshots the current graph first, so a rollback
is always reversible. Each run stores the executed graph, the resolved run context, and a
per-node record (input, output, error, timing) you can inspect after the fact.

Because every value is persisted, the editor doesn't need a live run to show data:
opening a workflow loads the **latest recorded output of each node across all past runs**
(`GET /{id}/node-outputs`), so clicking an arrow shows the fields and payload that flowed
through it historically — with a "data from a past execution" note and its timestamp.
A fresh run simply replaces those values with live ones.

**Run from this node (partial runs)**: select a node and press **▶ Run from this node**
in the inspector. Only that node and its downstream subgraph execute; every upstream node
is seeded from its latest persisted output, so expressions like `$node.<id>.output.…`
keep resolving without re-calling external tools. The run is recorded with
`trigger_type: partial` (API: `POST /{id}/run` with `start_node_id`). Handy while wiring
up the tail of a pipeline whose expensive head already ran.

**Replay a run**: any finished run (completed, failed or cancelled) shows a **↻ Replay**
button in the Runs view detail panel. It re-runs the workflow with the *exact trigger
payload* of that run against the workflow's **current** graph — so after fixing a node you
can reproduce the original input in one click and confirm the fix (API: `POST
/v1/graph-workflows/runs/{rid}/replay`). Partial runs can't be replayed (they have no full
trigger payload) and return `409`.

**Test expression**: the inspector's *Test expression* panel evaluates any expression
(`={{ … }}`, `{{ … }}` or `=py:`) read-only against the latest run data — `$node` from
the latest persisted outputs, `$trigger` from the most recent run — and shows the
resolved value or the error message inline (API: `POST /{id}/preview-expression`).
Use it to debug a path before wiring it into a param.

**Export**: the *Export* button (or `GET /{id}/export`) downloads the workflow as a
portable JSON snapshot (`{ kind, schema_version, name, description, graph, … }`). Since
fase 5.2 the snapshot also carries a `secrets` array — the **names** of every
`$secrets.<name>` the graph references (values never travel), so the importer knows which
secrets to re-create in the target environment.

**Import** (fase 5.2): the 📥 button next to **New** picks a `.workflow.json` file — the
exact file **Export** produces — and creates a new workflow via the dedicated
`POST /v1/graph-workflows/import` endpoint, opened immediately for editing. The import is
**validated**: the graph schema and node-count limit are enforced (400 on violation),
while non-blocking issues surface as warnings shown as toasts — unknown node types (a
tool or MCP server not available here), edges referencing missing nodes, and `$secrets`
references not defined in this profile. Export-only fields are accepted and ignored, so
any exported file round-trips cleanly.

**Sharing across workspaces** (fase 5.2): a workflow can be shared into a Phase 20
workspace like conversations and KB documents — `POST /v1/workspaces/{ws}/workflows`
(`{ workflow_id }`, editor role + ownership required), `GET` lists what's shared,
`DELETE /{ws}/workflows/{wid}` unshares. Any member can then **import a copy** into
their own profile via `POST /{ws}/workflows/{wid}/import` — the copy is named
"… (shared)" and comes back with the same validation warnings as a file import
($secrets values never travel; the references must be re-satisfied by the importer).

### Metrics & observability (fase 5.1)

`GET /v1/graph-workflows/stats` aggregates per workflow: run counts by outcome
(completed / failed / cancelled), **success rate** over terminal runs, **average
duration**, and the **LLM token totals** summed from the `_usage` key that `llm.*` node
runs report. The **Runs view** renders it as a dashboard strip (runs, success rate, avg
duration, tokens in/out) that follows the workflow filter, and the run detail shows the
opened run's total tokens next to its duration. No cost figures are invented: tokens are
reported as-is, since no per-model price table exists in the repo.

### Generate a workflow from a description (fase 5.3)

The 🪄 button above the workflow list opens a **"describe what you want"** dialog:
`POST /v1/graph-workflows/generate` hands the node catalog (types, outputs, param names)
to the LLM, which must reply with a complete `{name, description, graph}` JSON. The
dialog exposes the same **model picker** as the `llm.*` nodes plus an optional
**failover chain** (Settings → Models), so generation can use any provider/model and
fall back through a chain on call failure. The reply is **validated and normalized** —
unknown node types and broken edges are dropped (with warnings), a missing trigger gets
a `manual` node prepended, and nodes without positions get a layered auto-layout — then
the draft opens in the editor for review. Nothing runs until you save and activate it.

The UI calls the streaming twin `POST /v1/graph-workflows/generate/stream`, which emits
`log` SSE events at each stage — catalog loaded (N node types), model called, reply
received (model + cache status), graph validated (nodes/edges kept, warnings), trigger
added, layout applied — so the dialog shows a **live progress log** instead of a bare
spinner, followed by a `done` event with the draft (or `error` with the reason).

## API

Everything the UI does is available under `/v1/graph-workflows` (JWT-protected), so a graph
can be created and run entirely from JSON with no UI:

```
GET    /v1/graph-workflows/node-types
GET    /v1/graph-workflows/secrets         (profile secrets — names only, never values)
PUT    /v1/graph-workflows/secrets         { name, value }   (upsert; $secrets.<name>)
DELETE /v1/graph-workflows/secrets/{name}
GET    /v1/graph-workflows
POST   /v1/graph-workflows                 { name, description?, graph:{nodes,edges}, variables? }
GET    /v1/graph-workflows/{id}
PATCH  /v1/graph-workflows/{id}            { name?, description?, graph?, active?, variables? }
DELETE /v1/graph-workflows/{id}
POST   /v1/graph-workflows/{id}/activate | /deactivate
POST   /v1/graph-workflows/{id}/run        { payload, start_node_id? }
POST   /v1/graph-workflows/{id}/nodes/{nid}/test   { input?, node? }   (single-node test, fase 3.1)
GET    /v1/graph-workflows/runs         ?status=&workflow_id=&limit=   (profile-wide registry)
POST   /v1/graph-workflows/runs/{rid}/cancel   (stop a queued/pending/running/waiting run)
POST   /v1/graph-workflows/runs/{rid}/replay   (re-run with this run's trigger payload)
GET    /v1/graph-workflows/approvals        ?status=&run_id=   (human-approval requests, fase 4.4)
POST   /v1/graph-workflows/approvals/{aid}/decision   { approved, comment? }
GET    /v1/graph-workflows/stats            (per-workflow metrics: runs, success rate, tokens — fase 5.1)
POST   /v1/graph-workflows/import           (portable snapshot → workflow + validation warnings — fase 5.2)
POST   /v1/graph-workflows/generate         { prompt, model?, failover_chain? } → validated draft graph, NOT saved (fase 5.3)
POST   /v1/graph-workflows/generate/stream  (same, streaming: `log` SSE events, then `done`/`error`)
GET    /v1/workspaces/{ws}/workflows        (workflows shared into a workspace — fase 5.2)
POST   /v1/workspaces/{ws}/workflows        { workflow_id }   (share; editor+ and owner)
DELETE /v1/workspaces/{ws}/workflows/{wid}  (unshare)
POST   /v1/workspaces/{ws}/workflows/{wid}/import   (copy a shared workflow into my profile)
GET    /v1/graph-workflows/{id}/runs
GET    /v1/graph-workflows/{id}/node-outputs   (latest persisted output per node, all past runs)
GET    /v1/graph-workflows/{id}/export         (portable JSON snapshot)
GET    /v1/graph-workflows/{id}/versions
POST   /v1/graph-workflows/{id}/versions/{v}/restore
POST   /v1/graph-workflows/{id}/triggers   { type, config?, enabled? }
DELETE /v1/graph-workflows/triggers/{tid}
GET    /v1/graph-workflows/runs/{rid}      (+ node_runs)
GET    /v1/graph-workflows/runs/{rid}/stream   (SSE live view)
POST   /v1/wf/hooks/{token}                (public webhook receiver)
```

Settings: `GRAPH_WORKFLOW_SCHEDULER_ENABLED` (default on) toggles the schedule poll loop;
`GRAPH_WORKFLOW_MAX_NODES` bounds a single graph; `GRAPH_WORKFLOW_FILES_DIR` is the
workspace storage root for the `file.*` / sqlite `db.query` nodes (fase 4.2);
`GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT` caps how long a `human.approval` node may wait
(fase 4.4, default 7 days).
