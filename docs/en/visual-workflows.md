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

> **In a hurry?** Click ✨ on the `/graph-workflows` page and **Import** one of the
> twenty-five ready-made [example graphs](../examples/graph-workflows.md) — one per
> feature (logic, loops, data, files, DB, notifications, AI, chat, triggers) — it opens
> on the canvas ready to edit and run.


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
| **Trigger** | `manual`, `schedule`, `webhook`, `event`, `error`, `success` (another workflow completed — fase 6.1), `file.watch` / `email.inbound` (poll-based external-world triggers — fase 6.2) |
| **Action** | `tool.<name>` — any **built-in** tool (RSS, read_url, weather, kb_search, http_request, python_exec…) · `http.request` (generic HTTP call, per-host rate-limited — fase 6.6) · `subworkflow` (run another workflow inline, with I/O contracts — fase 6.4) · `workflow.<id>` (a contract-declaring workflow as a typed node — fase 6.4) · `human.approval` (suspend until a human approves/rejects — fase 4.4) · `human.input` (suspend until a human fills a JSON-Schema form — fase 10.1) · `wait.event` (suspend until a correlated external event arrives — fase 10.2) |
| **MCP & custom** | every **discovered MCP server tool** (`tool.mcp__<server>__<tool>`) and the profile's **custom HTTP tools** (`tool.custom__<name>`) appear as drag-in nodes — no code per tool |
| **Logic** | `if` (true/false branch), `switch` (case branches), `merge` (collect inputs), `for` (for-each over an array), `repeat` (N times), `while` (condition-driven loop with an iteration cap — fase 6.3), `wait` (pause for N seconds or until a point in time) |
| **Data** | `set` (build an object), `filter` (keep matching array items), `code` (Python sandbox), `aggregate` (reduce an array — sum/avg/min/max/count/concat over a field), `batch` (split an array into fixed-size chunks), `db.query` (parameterised SQL — sqlite/postgres), `file.read` / `file.write` (workspace storage), `file.parse` (parse in-flight JSON/CSV/lines) |
| **Notify** | `notify.telegram` (linked Telegram chat), `notify.email` (SMTP), `notify.webhook` (Slack/Discord/ntfy/any webhook), `notify.inapp` (web UI bell, zero config) |
| **AI** | `llm.completion` (one provider call), `llm.agent` (the full Phase 18 agent loop, with access to built-in + MCP + custom tools), `llm.classify` / `llm.extract` (guaranteed-structured output — fase 4.1), `llm.judge` (rubric-scored quality gate with pass/fail handles — fase 18.1), `kb.search` (structured semantic search over the knowledge base — fase 6.5) |

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

**Per-host rate limiting (fase 6.6)** — calls are throttled per host through a
process-wide sliding one-minute window: set `maxRequestsPerMinute` on the node and/or a
global per-domain map in `GRAPH_WORKFLOW_RATE_LIMITS` (`host=rpm` pairs or a JSON object;
the stricter cap wins). Over-cap requests **wait, they don't fail**, and the wait shows up
as `rate_limited_s` in the node output (visible in the node run). This protects external
APIs called by parallel runs — complementary to the per-workflow run queue (fase 2.3).
`notify.webhook` routes through `http.request`, so the same limits apply to it.

### Composition — `subworkflow`

Runs **another workflow of the same profile inline** as a child run and returns when it
finishes. Parameters: `workflow_id` and an optional `payload` (JSON object) that becomes
the child's `$trigger`; without a payload, this node's input is passed as
`{ input: … }`. The output is `{ run_id, workflow_id, status, output }`, where `output`
is the child's **sink node output** (or a map of them when the child has several sinks).
The child executes as a normal, fully observable run (`trigger_type: subworkflow`) with
its own node records and SSE stream. Nesting is capped at **5 levels** and self-recursion
fails the run rather than looping forever.

**Contracts (fase 6.4)** — a workflow can declare an `input_schema` and/or
`output_schema` (JSON Schema, **Contracts** section in the run panel; they travel with
export/import). When set, the `subworkflow` node **validates the payload before the child
run** and the **sink output on return** — a violation fails the node with the exact
mismatch (the validator covers `type`, `required`, `properties`, `items`, `enum`).
Workflows with an input contract also appear in the palette as typed **`workflow.<id>`
nodes** whose params mirror the contract's properties — and the LLM generator (fase 5.3)
sees them in its catalog context, so it can **compose existing workflows** instead of
regenerating them.

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

### Quality gate — `llm.judge` (fase 18.1)

Scores another node's output against a **rubric** and gates on the result, so you can
*measure* LLM quality instead of hoping. Parameters: `input` (an expression; the content to
judge, defaults to the node input), `criteria` (the rubric — required), `scaleMax` (the
1..N score scale, default from `GRAPH_WORKFLOW_JUDGE_DEFAULT_SCALE_MAX`), `threshold`
(pass when `score ≥ threshold`; default 60% of the scale), an optional `reference` answer
and extra `instructions`. Output: `{ score, verdict, passed, rationale, model, _usage }`.

The node has **two handles**, `pass` and `fail`, chosen by the threshold — and the
threshold is **authoritative**: the model's own `verdict` never overrides the gate. Wire
`pass` to publish/notify and `fail` to a review path, or feed `fail` back into the generator
through a `while` loop for a *generate → judge → regenerate* cycle. Shares the model picker,
failover chain and response cache with the other `llm.*` nodes; the judge model can differ
from the generator's. A missing `criteria` or a non-numeric score raises, so retry / *On
error* apply. See the curated **LLM quality gate** example.

### Prompt A/B testing (fase 18.2)

Any `llm.*` node can carry **variants** to compare prompts/models head-to-head. Set
`variants` to a JSON array of `{name, weight?, params:{overrides}}` and pick a
`variantStrategy`:

- **`round-robin`** (default) — alternates evenly across runs via a per-node counter
  persisted in the workflow state, so the split survives a backend restart.
- **`weighted`** — samples by each variant's `weight` (all-zero weights fall back to
  uniform).

Each run overlays the chosen variant's params onto the node's own and stamps the choice on
the output (`_variant`). `GET /v1/graph-workflows/{id}/nodes/{node_id}/variants` then breaks
the run history down **per variant** — executions, ok-rate, mean `llm.judge` score,
pass-rate and tokens — and flags the leading variant as `winner` (highest average score,
else ok-rate), the basis for a "promote variant" decision.

### Knowledge-base search — `kb.search` (fase 6.5)

A dedicated bridge to the knowledge base (Phase 28): semantic search over the workspace
documents **from inside a workflow**, with structured output instead of the flattened text
the `tool.kb_search` node returns. Parameters: `query` (expression; defaults to the node
input), `top_k` (default 5, max 20) and an optional `document_ids` filter (JSON array or
comma-separated ids) to scope the search to specific documents. Output:
`{ results: [{text, score, source, chunk_index}], count }` — feed the hits straight into
an `llm.completion` prompt for RAG inside workflows, no generic `llm.agent` needed.

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
GET  /v1/graph-workflows/approvals                 ?status=pending&run_id=&kind=   (list)
POST /v1/graph-workflows/approvals/{aid}/decision  { approved: true|false, comment? }
```

### Advanced human-in-the-loop — `human.input`, `wait.event` (fase 10)

Two more nodes suspend the run (`waiting`) the same way `human.approval` does, generalising
its request row into a `kind` (`approval` | `input` | `event`) so all three share the same
poll/resume loop and survive a backend restart identically.

**`human.input`** — the request carries a **form defined by JSON Schema** (`schema` param:
fields, types, `required`, `enum`). Decide from the Runs view (the fields render as a form)
or via API; the submitted `data` is **validated against the schema** before it is accepted.
The run resumes on the **`submitted`** branch with `{ data, status, comment, decided_by }`
as output; a timeout follows `onTimeout` (`branch` routes through the **`timeout`** branch,
`fail` fails the node). Unlocks "ask the operator for the missing value" flows — e.g. an
expense amount and category before continuing.

```
POST /v1/graph-workflows/approvals/{aid}/submit  { data: {...}, comment? }
```

**`wait.event`** — the run suspends until an **external system** delivers an event with a
matching **correlation id**. `correlationId` (expression, e.g. an order id from `$trigger`)
names the key; `POST /v1/graph-workflows/events/{correlation_id}` (authenticated,
profile-scoped) wakes the run and delivers its `payload` as the node's **output**, through
the **`main`** branch. Same `timeout` / `onTimeout` (`branch` | `fail`) as `human.input`.
Covers real async callbacks — payments, digital signatures, tickets, third-party webhooks —
without polling. A `waiting` run does not hold a `max_concurrent_runs` slot.

```
POST /v1/graph-workflows/events/{correlation_id}  { payload: {...} }
```

Params (both nodes): `title`, `message` (expression), `timeout` (seconds, default 24 h,
capped by `GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT`), `onTimeout`. `human.input` additionally
takes `schema` (the form's JSON Schema); `wait.event` takes `correlationId` instead.

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

**`while` (fase 6.3)** — a third loop node for condition-driven repetition (polling an
async API, cursor pagination) without subworkflow recursion. Its `condition` (same
expression syntax as `if`) is **re-evaluated before every iteration** with `$item` bound
to the **previous iteration's body output** (the node input on the first pass) and
`$index` to the iteration number — so a body that returns `{ done, cursor }` can be
driven by `condition: ={{ not $item.done }}`. A **mandatory iteration cap** applies:
`maxIterations` (default 100), hard-limited by `GRAPH_WORKFLOW_WHILE_MAX_ITERATIONS`
(default 1000). Output on `done`: `{ items, count, capped }` (`capped` is true when the
loop stopped because of the cap, not the condition).

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
- **Success** (fase 6.1) — the mirror of the error trigger: fires when another workflow's
  run **completes successfully**, with the same `config.workflow_id` filter and anti-loop
  guards. The payload is `{workflow_id, workflow_name, run_id, output}`, where `output` is
  the completed run's sink output — "A then B" pipelines without subworkflows. Schedules
  may also carry **multiple cron expressions** (`config.crons`, one per line in the UI):
  the next run is the earliest across all expressions, for mixed timetables (e.g. weekdays
  9–18 + reduced weekends) on a single trigger.
- **File watch** (fase 6.2) — poll-based (reuses the schedule loop, no inotify): watches a
  subfolder of the workspace storage (`config.path`) with a glob `config.pattern`; fires
  per created/modified file with `$trigger = {path, event, size}` (`path` relative to the
  storage root, directly consumable by `file.read`). The first poll only seeds the
  snapshot; per-trigger `config.interval` is floored by `GRAPH_WORKFLOW_WATCH_POLL_SECONDS`
  (default 60 s).
- **Inbound email** (fase 6.2) — polls an IMAP inbox for unseen messages:
  `config = {host, port, folder, username, password_secret, from, subject, interval}`.
  Credentials come from `$secrets` (`password_secret` names the secret — the password is
  never stored in the trigger). Sender/subject are case-insensitive substring filters.
  `$trigger = {from, subject, body, attachments}`; attachments are saved under
  `email_attachments/` in the workspace storage, readable with `file.read`.

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

**Retry from the failed node** (fase 7.1): failed runs show a **↺ Retry** button (list and
detail panel). Unlike Replay — which starts over with the original trigger against the
current graph — Retry creates a new run over the **origin run's exact graph snapshot**,
seeded with the node outputs already checkpointed by that run: only the failed node and
its downstream subgraph re-execute (the same mechanics as the crash resume of fase 2.4,
on explicit request). Both retried and replayed runs record `origin_run_id`, shown as a
"derived from run …" line in the detail panel (API: `POST /v1/graph-workflows/runs/{rid}/retry`,
`409` unless the run is `failed`).

### Environments — dev/prod without duplicating the graph (fase 7.2)

The run panel's **Environments** section defines named environments on the workflow as a
JSON map — `{"prod": {"vars": {...}, "secrets": {"TOKEN": "TOKEN_PROD"}, "version": 5}}`:

- **vars** overlay the workflow `$vars` for runs in that environment (e.g. a different
  endpoint or greeting per environment);
- **secrets** remap `$secrets.<alias>` to another stored secret — the graph keeps
  referencing `$secrets.TOKEN` and the prod environment binds it to `TOKEN_PROD`
  (values never appear anywhere; bindings are names only);
- **version** pins the graph version runs in that environment execute. The **⇧ Promote**
  button (API: `POST /{id}/environments/{env}/promote`, optional `{version}`) pins the
  current version — "promote to prod" while the editor keeps working on the current graph.

Pick the environment for a manual run in the run panel (or pass `environment` in the
`POST /{id}/run` body); schedule and webhook triggers may pin one with `"environment":
"prod"` in their config. Every run records the environment it executed in (badge in the
Runs view). Environments travel with export/import — vars and secret aliases are plain
config; a pinned version applies again only after promoting in the target environment.

### Audit trail and share roles (fase 7.3)

`GET /v1/graph-workflows/{id}/audit` returns the workflow's audit trail — who created,
modified, activated/deactivated, executed, replayed/retried, exported/imported, decided
approvals and promoted environments, newest first. The **Health** tab renders it under
the node metrics.

Sharing a workflow into a workspace now carries a **role** (`{ workflow_id, role }`):

| Share role | Members may |
|---|---|
| `viewer` | inspect the shared definition and import a copy (previous behaviour) |
| `editor` | …plus launch runs — `POST /v1/workspaces/{ws}/workflows/{wid}/run`; the run executes under the **owner's** profile ($secrets/$vars included) and shows in their registry |
| `approver` | …plus decide the workflow's `human.approval` requests via the standard decision endpoint |

Re-sharing the same workflow updates the role in place. The owner keeps implicit admin.

### Per-node health metrics (fase 7.4)

`GET /v1/graph-workflows/{id}/stats/nodes` aggregates the run history **per node**:
executions by outcome (ok / error / skipped), **error rate**, average / **p50** / **p95**
duration, LLM tokens and last execution — sorted unhealthiest first. The workflow shell's
new **Health** tab renders the table (error rates highlighted) so retry/timeout tuning
(fase 2.1) starts from data, not guesses.

### Approve from Telegram (fase 7.5)

When a `human.approval` node has `telegram` enabled, the Telegram notification now carries
inline **✅ Approve / ❌ Reject** buttons. The bot verifies the chat is linked to the
profile that owns the request, then settles it exactly like
`POST /approvals/{id}/decision` — first writer wins against the web UI and the timeout —
and the suspended run resumes within seconds. The message is edited in place with the
outcome.

### Advanced editor — diff, notes, step debugging (fase 8)

**Visual diff between versions (fase 8.1)** — the run panel's **Versions** section has a
*Compare* row: pick two saved versions and hit **Diff**. The editor projects the
difference onto the current canvas — **added** nodes glow green, **changed** nodes
(different config) glow yellow — and a diff bar shows the counts and the list of
**removed** nodes (they aren't on the current graph). A node's canvas *position* is
deliberately ignored: moving a node is not a change. API:
`GET /{id}/versions/{a}/diff/{b}` → `{ added_nodes, removed_nodes, changed_nodes:
[{id, before, after}], unchanged_nodes, added_edges, removed_edges }`. With environments
(7.2) this answers "what actually changes when I promote to prod".

**Notes and frames (fase 8.2)** — the toolbar's **📝 Note** and **▢ Frame** buttons drop
annotations on the canvas: sticky notes (free text) and frames (labelled rectangles to
group nodes visually). Drag them anywhere, double-click to edit the text (clearing it
deletes the note). They are saved with the graph, kept in the version history and carried
by export/import — but the **engine ignores them entirely**: they never run and never
affect execution. Use them to document a graph for the next person who opens it.

**Step-by-step debugging (fase 8.3)** — click **🐞 Debug** in the toolbar to enter debug
mode. Each node grows a small dot in its top-left corner: click it to set a **breakpoint**.
Press **Start debug run** — the run is created **paused**, before any node executes. Then:

- **⏭ Step** runs the next node and pauses again (the node about to run is highlighted in
  purple, and the debug bar shows its resolved input);
- **▶ Continue** runs until the next breakpoint (or the end);
- **⏹ Stop** cancels the run.

Under the hood this reuses the crash-resume machinery: each command re-executes from the
run's checkpoint, runs one node and re-pauses (API: `POST /{id}/run` with `debug:true`,
then `POST /runs/{id}/debug` with `{command: "step"|"continue"|"stop", breakpoints?,
input?}`). The optional `input` mocks the next node's primary input — handy to try a
payload without re-wiring upstream nodes. A run left paused longer than
`GRAPH_WORKFLOW_DEBUG_MAX_PAUSE` (default 1 h) is auto-cancelled so nothing stays
suspended forever. Paused runs show a purple **paused** chip in the Runs view.

**Test expression**: the inspector's *Test expression* panel evaluates any expression
(`={{ … }}`, `{{ … }}` or `=py:`) read-only against the latest run data — `$node` from
the latest persisted outputs, `$trigger` from the most recent run — and shows the
resolved value or the error message inline (API: `POST /{id}/preview-expression`).
Use it to debug a path before wiring it into a param.

**Export**: the *Export* button (or `GET /{id}/export`) downloads the workflow as a
portable JSON snapshot (`{ kind, schema_version, name, description, graph, … }`). Since
fase 5.2 the snapshot also carries a `secrets` array — the **names** of every
`$secrets.<name>` the graph references (values never travel), so the importer knows which
secrets to re-create in the target environment. Since fase 7.2 the snapshot also carries
`environments` — the workflow's named environments (`$vars` overlays and `$secrets`
**alias** bindings only; a pinned `version` doesn't apply in the target environment until
promoted there again, since version numbers aren't portable across workflows).

**Import** (fase 5.2): the 📥 button next to **New** picks a `.workflow.json` file — the
exact file **Export** produces — and creates a new workflow via the dedicated
`POST /v1/graph-workflows/import` endpoint, opened immediately for editing. The import is
**validated**: the graph schema and node-count limit are enforced (400 on violation),
while non-blocking issues surface as warnings shown as toasts — unknown node types (a
tool or MCP server not available here), edges referencing missing nodes, and `$secrets`
references not defined in this profile. Export-only fields are accepted and ignored, so
any exported file round-trips cleanly — including the `environments` field added in fase 7.2.

**Sharing across workspaces** (fase 5.2, roles extended in fase 7.3): a workflow can be
shared into a Phase 20 workspace like conversations and KB documents —
`POST /v1/workspaces/{ws}/workflows` (`{ workflow_id, role? }`, editor role + ownership
required to share; `role` defaults to `viewer` — see [Audit trail and share
roles](#audit-trail-and-share-roles-fase-73)), `GET` lists what's shared,
`DELETE /{ws}/workflows/{wid}` unshares. Any member can then **import a copy** into
their own profile via `POST /{ws}/workflows/{wid}/import` — the copy is named
"… (shared)" and comes back with the same validation warnings as a file import
($secrets values never travel; the references must be re-satisfied by the importer;
`environments` travels the same way as in a file export/import).

### Metrics & observability (fase 5.1)

`GET /v1/graph-workflows/stats` aggregates per workflow: run counts by outcome
(completed / failed / cancelled), **success rate** over terminal runs, **average
duration**, and the **LLM token totals** summed from the `_usage` key that `llm.*` node
runs report. The **Runs view** renders it as a dashboard strip (runs, success rate, avg
duration, tokens in/out) that follows the workflow filter, and the run detail shows the
opened run's total tokens next to its duration. No cost figures are invented: tokens are
reported as-is, since no per-model price table exists in the repo.

Since fase 7.2, the endpoint accepts an optional `?environment=<name>` query param that
scopes every aggregate to runs executed in that named [environment](#environments--devprod-without-duplicating-the-graph-fase-72)
— compare `prod` health against the unfiltered (all-environments) totals without a
separate call per run. A workflow with zero runs in the requested environment still
comes back with `runs: 0` rather than being dropped from the list.

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

### Workflows as ecosystem tools (fase 9)

A workflow doesn't have to sit behind a trigger — it can become a **building block**
other things call.

**Publish a workflow as a tool (fase 9.1).** Give the workflow an **input contract**
(run panel → *Contracts*), tick **Publish as a tool**, and **activate** it. It now shows
up as a callable tool named `workflow__<id>` — its description and parameters come
straight from the contract. From then on:

- an **`llm.agent`** node can call it like any built-in/MCP/custom tool;
- another workflow's **`tool.*`** node can call it;
- the product **chat** can call it.

Calling the tool runs the workflow inline as a normal run (so [metrics](#metrics--observability-fase-51)
and the [audit trail](#audit-trail-and-share-roles-fase-73) apply) and returns its sink
output to the caller. A **depth guard** (`GRAPH_WORKFLOW_TOOL_MAX_DEPTH`, default 3) stops
a workflow that (transitively) calls itself from recursing forever. `GET /tools` lists
everything the profile currently publishes.

**Expose to external MCP clients (fase 9.2).** The same published workflows are reachable
by external MCP clients (Claude Desktop, IDEs, …) through the product's own MCP server at
`POST /v1/graph-workflows/mcp` — a JSON-RPC 2.0 endpoint (`initialize` / `tools/list` /
`tools/call` / `ping`) authenticated with your normal credentials. A `tools/call` runs
the workflow inline (recorded with trigger origin `mcp`) and returns its output as MCP
`content`.

**Chatbot workflows — the `chat` trigger (fase 9.3).** Add a **`chat`** trigger and end
the graph with a **`chat.reply`** node, and the workflow becomes a conversational
endpoint: `POST /v1/graph-workflows/{id}/chat` with `{ message, session_id? }` runs it
with `$trigger = {session_id, message, history}` and returns `{ session_id, reply,
run_id }` (the `chat.reply` node's text). Session history persists across turns and is
purged after `GRAPH_WORKFLOW_CHAT_SESSION_TTL` of inactivity. One run per message.

**OpenAPI import (fase 9.4).** `POST /v1/graph-workflows/openapi/import` with a spec
(inline `spec` or a `url`, optional `path_prefix`) turns each operation into a
preconfigured **`http.request`** node draft — method, URL (server + path), query
parameters, and auth mapped onto `$secrets` placeholders (a bearer scheme →
`Authorization: Bearer {{ $secrets.API_TOKEN }}`, an apiKey header → that header). The
nodes come back unsaved for you to drop onto the canvas; the count is capped by
`GRAPH_WORKFLOW_OPENAPI_MAX_OPERATIONS`.

### Testing, dry-run and cost estimate (fase 11)

Treat a workflow like code: saved regression tests, a safe full-graph rehearsal, and a
before-you-schedule-it cost projection. All three live in the run panel's **Tests &
dry-run** section.

**Test suites (fase 11.1).** Save a **test case**: a fixture `$trigger` payload plus
**assertions** on the output of any node (`equals`, `contains`, `json_path`, or a JSON
`schema` check). Click **Run tests** to execute every saved case as a real, observable
run and see green/red per assertion with the actual vs. expected value. Give an
external-effect node (`http.request`, `db.query`, `notification.*`/`email.*`, `llm.*`) a
**pinned output** (fase 3.2) and a test case exercising it becomes fully deterministic —
no live endpoint, no LLM spend; a node without a pin still makes the real call.

**Full dry-run (fase 11.2).** Click **Run dry-run** to simulate the whole graph with the
current run payload: every external-effect node is mocked — its pin if it has one,
otherwise a typed placeholder shaped like the real output (`{status, headers, body}` for
`http.request`, `{text, _usage}` for `llm.completion`, …) — so **nothing external ever
happens**. The report shows the execution path, every node's simulated output, and which
nodes a real run would have had a side effect on. Run one before flipping a new graph's
schedule **Active**.

**Cost estimate (fase 11.3).** The panel also shows a static **tokens/month** projection:
the graph's `llm.*` node count × the historical average tokens per run (from past runs'
`_usage` totals) × the workflow's active schedule frequency. No invented price list —
tokens only; the accompanying note explains what the number does (and doesn't) account
for when there isn't enough history or no active schedule yet.

### Budgets, retention & redaction (fase 12)

Guardrails before pushing a schedule + LLM combo into production, alongside the audit
trail and share roles (fase 7.3).

**Budgets and quotas (fase 12.1).** Set a monthly **token cap** and/or **run cap** on a
workflow (run panel → **Budget & quotas**, under Tests & dry-run) and/or a profile-wide
cap (`GET/PUT /v1/graph-workflows/budget`) that applies on top across every workflow.
Usage is measured over the current UTC calendar month from the same run history the
fase 5.1 stats already track — nothing to reset by hand, the period rolls over for free.
Reach either cap and new runs of that workflow stop: a manual run is rejected outright
with an explicit error, and a schedule/event trigger that keeps firing into an exhausted
budget auto-disables after its usual run of consecutive failures (the same mechanism
that already retires a broken trigger). Crossing 80% of a cap (configurable via
`GRAPH_WORKFLOW_BUDGET_WARN_PCT`) raises a one-time in-app notification for the period so
you see it coming.

**Retention and redaction (fase 12.2).** Give a workflow its own run-history retention
window in days, or leave it on the instance-wide default
(`GRAPH_WORKFLOW_RUNS_RETENTION_DAYS`, 0 = keep forever); a background sweep purges
finished (completed/failed/cancelled) runs past the cutoff — a run still in progress or
waiting on a human is never touched. For a node whose output carries something sensitive,
list its dotted field paths (e.g. `body.card_number`) in the inspector's **Redact** field:
those fields are masked as `***` wherever the output is persisted, streamed live, or
exported — but the real value is still what the *next* node sees, so a masked field can
still drive downstream logic during the run itself.

### Copilot and workflow-as-code (fase 13)

**Expression autocomplete (fase 13.1).** Start typing `$node.` in any expression field
and the inspector proposes the ids of nodes upstream of the one you're editing; once you
pick one, `.` completes with that node's real output fields (from a pinned output or its
last run). `$vars.` and `$secrets.` complete the same way against your workflow's declared
variables and secret *names* — never their values — and `$item`/`$index` show up for a
node sitting inside a for/repeat body.

**Explain / repair (fase 13.2).** When a run fails, the failed node in the run panel gets
an **Explain / repair** button. It sends that node's type, current params, the input it
received and the error to the LLM, which comes back with a short plain-language cause and,
when it's confident of a concrete fix, a corrected params object shown as a diff. Nothing
is applied automatically — **Accept** merges it into the node in the canvas (you still
save normally afterwards) and **Discard** drops it.

**Git sync of definitions (fase 13.3).** Point a workflow at a Git repo (run panel →
Versions → **Git sync**: repo URL, branch, a `$secrets` name holding the access token,
and an optional path inside the repo) and every version you save from then on is
committed there as JSON — one commit per version, message naming the version and who
saved it. **Pull now** fetches the branch and, if the file changed there (e.g. someone
merged a PR), imports it as a new **draft** version — it never overwrites your live
graph, so you review/restore it like any other version.

### Remote execution and scalability (fase 14)

**Remote runners (fase 14.1).** Some work needs to happen somewhere other than the
backend process: an internal API only reachable from a customer's network, a database
that isn't exposed publicly, a heavy `code` node that wants a bigger machine, local
inference on a GPU box. From **Graph workflows → Runners** register a runner (a name,
labels like `gpu`/`internal-network`/`dmz`, and an optional allow-list of node types it
may run) — you get a one-time token back, shown once. Start the agent process anywhere
outbound access reaches the backend:

```
SIBYL_RUNNER_TOKEN=<token> python -m app.runner.agent
```

It heartbeats and long-polls for work; nothing needs an inbound port opened to it. Give a
node a **runOn** label (Advanced settings) matching one of your runner's labels and it
executes there instead of on the backend — only for node types that need no backend
context (`http.request`, `code`, `db.query`, `set`, `if`, `switch`, `merge`, `filter`,
`aggregate`, `batch`, `wait`, `queue.publish`); anything referencing `$secrets` in its
params arrives at the runner already resolved to the literal value, never the vault.
No matching runner online within the timeout: **runOnFallback** `fail` (default) fails the
node like any other error (retry/On error still apply), `local` runs it on the backend
instead.

**`code` node sandbox (fase 14.2).** Nothing to turn on — the `code` node has always run
inside an isolated subprocess (CPU/memory/wall-clock limits, no network), on the backend
and identically on a remote runner.

**Engine scale-out (fase 14.3).** Behind the scenes, every run is "leased" to the process
instance executing it and the lease renews itself while the run is active; a lease left
behind by a crash is free for the next instance (including a restarted one) to pick up —
the same checkpoint/resume mechanism fase 2.4 already relies on. Nothing to configure on
a single-instance deployment; it's the seam a future multi-replica/Postgres deployment
would coordinate through.

**Message queue triggers (fase 14.4).** A **Queue publish** node sends a message to a
named topic; a **Queue consume** trigger on another (or the same) workflow fires once per
message it picks up, with `$trigger = {message, topic, headers}`. By default messages are
persisted (`GRAPH_WORKFLOW_QUEUE_DRIVER=db`) so nothing is lost across a restart; no
external broker is required. A real broker (RabbitMQ/Kafka/MQTT) can be wired in later as
a drop-in replacement without touching the node or trigger.

**CLI (fase 14.5).** `python -m app.cli.sibyl_wf` drives the same REST API from a
terminal or a CI pipeline — `run <id>`, `export`/`import`, `test <id> <node_id>`,
`logs <run_id>` — authenticated with a bearer token (`SIBYL_API_KEY`).

### Connectors and multimodal nodes (fase 15)

**Curated connectors (fase 15.1).** A **Connectors** palette category ships hand-tuned
`connector.<service>.<operation>` nodes over `http.request`, with the endpoint, auth and
payload already wired: **Slack** / **Discord** (post message), **GitHub** / **GitLab**
(create issue), **Jira** (create issue), **Google Sheets** (append / read). Credentials
come from `$secrets` (e.g. the token field set to `={{ $secrets.SLACK_TOKEN }}`), never
hardcoded. Because they *are* `http.request` under the hood, retry/backoff, node test,
pins and per-host rate limits all apply; the output is the HTTP output plus `{operation}`.

**`ssh.exec` (fase 15.2).** Runs a command on a remote host over SSH — key or password
from `$secrets`, host allow-list via `GRAPH_WORKFLOW_SSH_ALLOWED_HOSTS` (empty = any),
per-command timeout. Output `{stdout, stderr, exit_code}`; a non-zero exit raises (so
retry / On error apply) unless **Allow non-zero exit** is set.

**`browser` (fase 15.3).** Headless-browser scraping/checks with Playwright: open a URL,
optionally wait for a CSS selector, then extract **text**, an **attribute**, or a
**screenshot** (saved to the workspace storage, readable by `file.*`). Runs in a worker
thread with a per-action timeout; requires `playwright` (+ a browser) in the backend image.

**`rss.read` trigger (fase 15.4).** Polls an RSS/Atom feed and fires **one run per new
entry**, deduped by guid, with `$trigger = {title, link, published, summary, guid}`. It
reuses the file.watch/queue poll loop; the first poll only seeds the seen-set so a backlog
never storms the engine (`GRAPH_WORKFLOW_RSS_MAX_ENTRIES` caps fires per poll). Attach it
with `{url, interval}`. Ideal for "news → LLM → notify" flows.

**`doc.convert` (fase 15.5).** Converts a PDF/DOCX/HTML/PPTX/… document from the workspace
storage to **markdown** via markitdown (already in the image for the KB), output
`{markdown, chars, path}`; `path` defaults to the node input, so it chains straight off a
`file.watch` `$trigger.path`. The remaining media nodes (`audio.transcribe`, `image.ocr`,
`image.generate`, `tts`) depend on provider-layer support and are deferred.

### State and execution semantics (fase 16)

**Persistent state across runs (fase 16.1).** Three **Data**-category nodes read and write a
per-workflow key/value store that **survives across runs**: `state.get` → `{key, value, found}`
(with an optional `default` when the key is missing/expired), `state.set` (its `value` defaults
to the node input), and `state.increment` (atomic numeric add, returns the new value — ideal
for counters and rate windows). Give a key a TTL with `ttlSeconds`; an expired key reads as
absent. The store is viewable and editable from the run panel — `GET/PUT/DELETE
/v1/graph-workflows/{id}/state` — with manual edits recorded in the audit trail, and it is
**never included in an export** (it lives in its own table, not the workflow definition).

**Trigger idempotency (fase 16.2).** Set a `dedupKey` expression on a **webhook** or **event**
trigger (e.g. `{{ $trigger.order_id }}`) and the same key delivered twice inside
`dedupWindowSeconds` returns the **original** `run_id` (HTTP 200, `deduped: true`) instead of
starting a second run — exactly-once handling for systems that retry deliveries. Keys are
stored with a TTL; the default window comes from `GRAPH_WORKFLOW_DEDUP_DEFAULT_WINDOW_SECONDS`.

**Compensations / saga (fase 16.3).** Wire a `compensate` edge out of a side-effecting node to a
small rollback subgraph. If the run **fails downstream**, the engine walks the completed nodes
in **reverse order** and runs each node's compensation branch, seeded with that node's own
output (e.g. release the stock reserved before a charge that later failed). Compensation node
runs are tagged `compensation: true` on the live stream; a failure inside a compensation marks
the run `failed` with a compound error. Fully opt-in — a graph without a `compensate` edge is
unaffected.

**Run priority (fase 16.4).** A `priority` on a run (from the trigger config `priority` or the
launch API `priority`) lets the per-workflow queue promote higher-priority runs first, FIFO
within the same priority — an interactive run can jump ahead of a batch backfill.

## Detailed examples per feature

Complete, reproducible recipes — one per engine area. Each gives the **goal**, the **graph
chain**, the **node-by-node configuration** with concrete values and expressions, the
**expected output**, and the **feature it demonstrates**. They're meant to be rebuilt by
hand on the canvas or adapted — swap the URLs/cities/APIs for your own. Many have a
one-click importable twin in the ✨ gallery (see
[example graphs](../examples/graph-workflows.md)).

> **Convention** — where you see `={{ … }}` it's an expression (evaluated); a bare value is
> a literal. Node ids (`rss`, `api`, `triage`…) are the ones you pick in the inspector and
> use in `$node.<id>.output` paths.

### 1. Morning RSS digest — schedule trigger + tool + LLM

**Goal:** every morning at 08:00, summarise a feed's front page into five bullets and build
a titled digest object.

**Graph:** `schedule → tool.fetch_rss → llm.completion → set`

**Nodes:**
- `schedule` (`schedule` trigger) — **Daily** pattern, time `08:00`. Remember: fires only
  when the workflow is **Active**.
- `rss` (`tool.fetch_rss`) — `url`: `={{ $vars.FEED }}` (set `FEED =
  https://hnrss.org/frontpage` in the *Variables* panel).
- `summary` (`llm.completion`) — model from the picker; `prompt`:
  ```
  Summarise this news into 5 concise bullets:
  ={{ $node.rss.output.result }}
  ```
- `digest` (`set`) — builds the object:
  - `title` → `Digest for ={{ $now }}`
  - `body` → `={{ $node.summary.output.content }}`

**Expected output:** `{ title: "Digest for 2026-07-20…", body: "• …\n• …" }`.

**Demonstrates:** schedule trigger, output→input piping via `$node.<id>.output`, `$vars`,
string interpolation, the trigger → action → AI → data chain.

### 2. Webhook → knowledge-base answer (RAG) — `$trigger` + HMAC signing

**Goal:** expose a public URL that answers a question **strictly** from retrieved KB
passages.

**Graph:** `webhook → kb.search → llm.completion → set`

**Nodes:**
- `webhook` (`webhook` trigger) — after saving, generate the signing secret with **Rotate
  secret** (shown once).
- `search` (`kb.search`) — `query`: `={{ $trigger.question }}`, `top_k`: `5`.
- `answer` (`llm.completion`) — `prompt`:
  ```
  Answer using ONLY these passages. If they're not enough, say so.
  Question: ={{ $trigger.question }}
  Passages: ={{ $node.search.output.results }}
  ```
- `out` (`set`) — `answer` → `={{ $node.answer.output.content }}`.

**How to call it** (workflow Active):
```bash
BODY='{"question":"how do I configure SMTP?"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | sed 's/^.* //')
curl -X POST https://your-host/api/v1/wf/hooks/$TOKEN \
     -H "X-Signature: sha256=$SIG" -H 'Content-Type: application/json' -d "$BODY"
```

**Demonstrates:** webhook trigger, reading `$trigger.<field>`, RAG with `kb.search`, HMAC
protection (a request without a valid header is rejected with 401 before it's even parsed).

### 3. Conditional branch — `if` + whitelisted expressions

**Goal:** check a web page and branch on whether a keyword appears.

**Graph:** `schedule → tool.read_url → if → set (true) | set (false)`

**Nodes:**
- `fetch` (`tool.read_url`) — `url`: `={{ $vars.PAGE }}`.
- `check` (`if`) — `condition`:
  `={{ 'sale' in lower($node.fetch.output.result) }}`.
- `hit` (`set`, **true** branch) — `alert` → `Found "sale" at ={{ $now }}`.
- `miss` (`set`, **false** branch) — `status` → `no change`.

**Expected output:** only one branch runs; the node on the unpicked branch is recorded as
`skipped`.

**Demonstrates:** `if`-routing, the `in` operator, the `lower()` function, mutually
exclusive branches.

### 4. API call with retry and error branch — try/catch on the canvas

**Goal:** call an external API, retry twice, and **alert** only if every attempt fails.

**Graph:** `manual → http.request → set (main) | notify.telegram (error)`

**Nodes:**
- `api` (`http.request`) — `method` `GET`, `url` `={{ $vars.API_URL }}`, `timeout` `60`.
  **Advanced** section: **Retries** `2`, **Backoff** `2` s **Exponential**, **On error →
  Route to error branch**.
- `ok` (`set`, **main** output) — `status` → `={{ $node.api.output.status }}`,
  `data` → `={{ $node.api.output.json }}`.
- `alert` (`notify.telegram`, **error** output) — `text`:
  `API unreachable: ={{ $node.api.output.error }}`.

**Expected output:** on success `main` carries `{ status, ok, headers, json, text }`; once
retries are exhausted, `{ error, input }` flows on the `error` handle and the `main` branch
is skipped. Node `api` is still recorded as **error** even when it routes the error branch.

**Demonstrates:** `http.request`, retry with exponential backoff, the *On error → error
branch* policy, `$vars`.

### 5. Multi-branch routing — `switch`

**Goal:** route by channel to one of three queues.

**Graph:** `manual → switch → set | set | set`

**Nodes:**
- `route` (`switch`) — `value`: `={{ default($trigger.channel, 'a') }}`; `cases`:
  `["a","b","c"]`. Output handles: `a`, `b`, `c`, `default`.
- three `set` nodes wired to their handles.

**Try it:** put `{"channel":"b"}` in **Run payload** → only branch `b` runs; an out-of-list
value hits `default`.

**Demonstrates:** multi-case `switch`, `default()`, manual run payload as `$trigger`.

### 6. For-each loop over an array — `loop` / `done` handles, `$item` / `$index`

**Goal:** for each URL in a list, fetch it and collect the titles.

**Graph:** `manual → set (list) → for → (loop) tool.read_url → set` · `(done) set`

**Nodes:**
- `urls` (`set`) — `list` → `={{ ['https://a.dev','https://b.dev'] }}` (a bare expression
  stays a native list).
- `loop` (`for`) — `items`: `={{ $node.urls.output.list }}`.
- body, wired to the **`loop`** handle:
  - `get` (`tool.read_url`) — `url`: `={{ $item }}` (inside the body use `$item`/`$index`,
    **not** `$node.loop.output`).
  - `title` (`set`) — `t` → `={{ slice($node.get.output.result, 0, 80) }}`.
- continuation, wired to the **`done`** handle:
  - `all` (`set`) — `titles` → `={{ $node.loop.output.items }}`.

**Expected output:** on `done`, `loop` yields `{ items: [...], count: 2 }`.

**Demonstrates:** `for`, per-iteration scope (`$item`/`$index`), body (`loop`) vs
continuation (`done`) split, result collection.

### 7. Condition-driven loop — `while` (pagination / polling)

**Goal:** fetch pages while the API returns a cursor.

**Graph:** `manual → while → (loop) http.request → set` · `(done) aggregate`

**Nodes:**
- `pager` (`while`) — `condition`:
  `={{ $index == 0 or $item.next != null }}`, `maxIterations`: `50`.
- body (`loop`):
  - `page` (`http.request`) — `url`:
    `={{ $vars.API }}?cursor=={{ default($item.next, '') }}`.
  - `norm` (`set`) — `items` → `={{ $node.page.output.json.items }}`,
    `next` → `={{ $node.page.output.json.next }}` (becomes next iteration's `$item`).
- `flat` (`aggregate`, on `done`) — `op` `concat` over the `items` field.

**Expected output:** on `done`, `{ items, count, capped }` (`capped: true` if the cap is
hit).

**Demonstrates:** `while` (condition re-evaluated before each pass with `$item` = previous
body output), `maxIterations` cap, `aggregate`.

### 8. Data pipeline — `set` + `filter` + `aggregate` with the `=py:` escape hatch

**Goal:** keep only large orders and sum their totals.

**Graph:** `manual → set → filter → aggregate → set`

**Nodes:**
- `orders` (`set`) — `list` →
  `={{ [{'id':1,'total':40},{'id':2,'total':150},{'id':3,'total':300}] }}`.
- `big` (`filter`) — `items`: `={{ $node.orders.output.list }}`; **keep** mask via the
  sandbox escape hatch: `=py:[o['total'] > 100 for o in input]`.
- `sum` (`aggregate`) — `op` `sum` over the `total` field.
- `out` (`set`) — `total` → `={{ $node.sum.output.result }}` (`450`).

**Demonstrates:** `filter` with a boolean mask, the `=py:` escape hatch (real
comprehension), `aggregate` (`sum/avg/min/max/count/concat`).

### 9. Composition with a contract — `subworkflow` + `input_schema`/`output_schema`

**Goal:** reuse an "enrich customer" workflow as a step of another, validating input and
output.

**Prerequisite** — in the child workflow, run panel → **Contracts**:
- `input_schema`: `{"type":"object","required":["email"],"properties":{"email":{"type":"string"}}}`
- `output_schema`: `{"type":"object","required":["score"]}`

**Graph (parent):** `manual → subworkflow → set`

**Nodes:**
- `enrich` (`subworkflow`) — **Workflow**: pick the child from the dropdown; `payload`:
  `={{ {'email': $trigger.email} }}`. The payload is validated against `input_schema`
  **before** the child run; the returned output against `output_schema`.
- `out` (`set`) — `score` → `={{ $node.enrich.output.output.score }}`.

**Expected output:** `{ run_id, workflow_id, status, output }` — `output` is the child's
sink-node output. Nesting caps at 5 levels; self-recursion fails the run.

**Demonstrates:** `subworkflow`, JSON-Schema I/O contracts, observable child run
(`trigger_type: subworkflow`). With an `input_schema`, the child also appears as a typed
**`workflow.<id>`** node in the palette.

### 10. Human approval gate — `human.approval`

**Goal:** hold a deploy until a person approves.

**Graph:** `manual → human.approval → notify.inapp (approved) | notify.inapp (rejected)`

**Nodes:**
- `gate` (`human.approval`) — `title`: `Deploy ={{ $trigger.subject }}`, `message`:
  `Confirm the release?`, `timeout`: `86400` (24 h), `onTimeout`: `reject`,
  `telegram`: `true` (inline chat buttons).
- `go` (`notify.inapp`, **approved** handle) — `title`: `Deploy approved`.
- `stop` (`notify.inapp`, **rejected** handle) — `title`: `Deploy rejected`.

**How to decide:** the run enters **`waiting`** state (purple chip). Open it from **Runs** →
**✓ Approve / ✕ Reject** (with comment), or via API:
```
POST /v1/graph-workflows/approvals/{aid}/decision  {"approved": true, "comment": "ok"}
```

**Expected output:** `{ approved, status, comment, decided_by }` on the chosen branch. The
wait survives restarts (checkpoints) and does **not** occupy a concurrency slot.

**Demonstrates:** HITL, `waiting` state, `approved`/`rejected` handles, web or Telegram
decision.

### 10a. Expense approval form — `human.input`

**Goal:** collect a validated amount + category before continuing.

**Graph:** `manual → human.input → notify.inapp (submitted) | notify.inapp (timeout)`

**Nodes:**
- `form` (`human.input`) — `title`: `Expense approval`, `schema`: `{ "type": "object",
  "required": ["amount", "category"], "properties": { "amount": {"type": "number"},
  "category": {"type": "string", "enum": ["travel", "meals", "software", "other"]} } }`,
  `timeout`: `86400`, `onTimeout`: `branch`.
- `logged` (`notify.inapp`, **submitted** handle) — body uses
  `={{ $node.form.output.data.category }}: ={{ $node.form.output.data.amount }}`.
- `expired` (`notify.inapp`, **timeout** handle).

**How to fill:** the run enters **`waiting`**; open it from **Runs** — the fields render from
the schema — or via API:
```
POST /v1/graph-workflows/approvals/{aid}/submit  {"data": {"amount": 42, "category": "travel"}}
```

**Expected output:** `{ data, status, comment, decided_by }` on `submitted` — `data` is
validated against `schema` server-side before it is accepted.

**Demonstrates:** HITL form collection, JSON-Schema validation, `submitted`/`timeout`
handles.

### 10b. Wait for payment — `wait.event`

**Goal:** suspend a checkout run until an external payment provider confirms it.

**Graph:** `manual → wait.event → notify.inapp (main) | notify.inapp (timeout)`

**Nodes:**
- `wait` (`wait.event`) — `correlationId`: `={{ $trigger.order_id }}`, `timeout`: `3600`,
  `onTimeout`: `branch`.
- `paid` (`notify.inapp`, **main** handle) — body: `={{ $node.wait.output }}`.
- `expired` (`notify.inapp`, **timeout** handle).

**How to deliver:** an external system (or a manual test) POSTs to the correlation id:
```
POST /v1/graph-workflows/events/ord-123  {"payload": {"paid": true}}
```

**Expected output:** the delivered `payload` becomes the node's output on `main`.

**Demonstrates:** correlation-id event delivery, real async callbacks without polling.

### 11. Ticket triage — `llm.classify` + `switch` + `file.write` CSV

**Goal:** label a ticket with guaranteed structure, route it, and log it.

**Graph:** `manual → llm.classify → switch → notify.inapp ×3` (+ `file.write`)

**Nodes:**
- `triage` (`llm.classify`) — `input`: `={{ $trigger.text }}`; `categories`:
  `billing, bug, question`. An out-of-list reply raises (so retries apply).
- `route` (`switch`) — `value`: `={{ $node.triage.output.category }}`; `cases`:
  `["billing","bug","question"]`.
- three `notify.inapp` on their handles.
- `log` (`file.write`) — `path`: `tickets/triage-log.csv`, `format`: `csv`, `append`: `true`,
  `content`: `={{ {'cat': $node.triage.output.category, 'text': $trigger.text} }}`.

**Try it:** payload `{"text":"my invoice is wrong"}` → category `billing`.

**Demonstrates:** `llm.classify` (guaranteed `{category, confidence}` output), `switch` on
the result, `file.write` CSV append into workspace storage.

### 12. Structured extraction — `llm.extract` with a JSON Schema

**Goal:** extract typed fields from free text.

**Graph:** `manual → llm.extract → db.query`

**Nodes:**
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

**Expected output:** `parse` → `{ data: {...}, model, _usage }` (top-level `required` keys
are verified; a non-conforming reply raises). `save` → `{ rows, count, rowcount }`.

**Demonstrates:** `llm.extract` with JSON Schema, parametrised `db.query` (`?` placeholders
for sqlite; the file lives in workspace storage).

### 13. Postgres query with safe credentials — `db.query` + `$secrets`

**Goal:** read rows from Postgres without ever putting the DSN in the graph.

**Prerequisite:** run panel → **Secrets** → add `PG_DSN` (encrypted at rest, never
exported).

**Graph:** `schedule → db.query → notify.email`

**Nodes:**
- `q` (`db.query`) — `driver`: `postgres`, `dsn`: `={{ $secrets.PG_DSN }}`,
  `query`: `SELECT id, email FROM users WHERE created_at > $1`,
  `params`: `={{ [$vars.SINCE] }}` (`$1…` placeholders for postgres).
- `mail` (`notify.email`) — `to`: `={{ $vars.OPS }}`, `subject`: `New users`,
  `body`: `={{ $node.q.output.count }} new: ={{ $node.q.output.rows }}`.

**Demonstrates:** postgres `db.query`, encrypted secrets (`$secrets`, resolved only during
the run, `***` in *Test expression*), parametrised placeholders.

### 14. Broadcast to every channel — `notify.*` in parallel

**Goal:** deliver a message to in-app, Telegram, email and webhook, with unconfigured
channels degrading gracefully.

**Graph:** `manual → set → notify.inapp + notify.telegram + notify.email + notify.webhook`

**Nodes:**
- `msg` (`set`) — `text` → `={{ $trigger.text }}`.
- the four `notify.*` wired in parallel to `msg`. On Telegram/email/webhook set **On error →
  Continue on main branch**, so an unconfigured channel (no linked chat, no SMTP) doesn't
  fail the run; the in-app bell always works.
- `notify.telegram` with `parse_mode`: `Markdown` if `text` comes from an `llm.*` node in
  CommonMark (`**bold**` is normalised to Telegram's `*bold*`).

**Demonstrates:** parallel fan-out, the four notification channels, the *Continue* policy for
fault tolerance.

### 15. Centralised alerting hub — `error` trigger

**Goal:** a watchdog workflow that alerts when **any other** workflow fails.

**Graph:** `error → set → notify.telegram`

**Nodes:**
- `error` trigger — run panel → **＋ error**; leave `config.workflow_id` **empty** to react
  to *every* failure (or set one to watch a single workflow). Activate the workflow.
- `fmt` (`set`) — `text` →
  `❌ ={{ $trigger.workflow_name }} node ={{ $trigger.failed_node }}: ={{ $trigger.error }}`.
- `send` (`notify.telegram`) — `text`: `={{ $node.fmt.output.text }}`.

**Expected output:** on every failed run elsewhere, this fires with
`$trigger = {workflow_id, workflow_name, run_id, error, failed_node}`.

**Demonstrates:** `error` trigger, loop-guarding (never reacts to its own failures,
error-triggered runs don't cascade). Mirror image: the `success` trigger for "A then B"
pipelines.

### 16. Autonomous agent inside a pipeline — `llm.agent`

**Goal:** hand an open-ended goal to the agent loop (with built-in + MCP + custom tools) and
deliver its answer.

**Graph:** `manual → llm.agent → notify.inapp`

**Nodes:**
- `agent` (`llm.agent`) — model from the picker; optional **Failover chain**; `goal`:
  `={{ default($trigger.goal, 'Research the latest on X and summarise it') }}`;
  `max_steps`: `8`.
- `bell` (`notify.inapp`) — `body`: `={{ $node.agent.output.content }}`.

**Expected output:** `{ content, _usage, _cache }`; `_usage` sums tokens across all agent
steps. A successful failover is sticky (later steps start from the model that worked).

**Demonstrates:** autonomy dropped in where needed, full tool registry access inside a
deterministic graph, `_usage`/failover.

### 17. dev/prod environments without duplicating the graph — `environments` + promote

**Goal:** the same graph with different endpoints and credentials across prod and dev.

**Setup** — run panel → **Environments**:
```json
{
  "prod": { "vars": {"API": "https://api.example.com"},
            "secrets": {"TOKEN": "TOKEN_PROD"}, "version": 5 },
  "dev":  { "vars": {"API": "https://staging.example.com"},
            "secrets": {"TOKEN": "TOKEN_DEV"} }
}
```
A node reads `={{ $vars.API }}` and `={{ $secrets.TOKEN }}`: the environment overlay
overrides `$vars` and remaps the `$secrets` aliases (names only, never values).

**Promote:** **⇧ Promote** (`POST /{id}/environments/prod/promote`) pins the current version
to `prod` while you keep working on the graph. Pick the environment on a manual run
(`environment` field) or in a trigger's config; every run records its badge.

**Demonstrates:** named environments, `$vars` overlay / `$secrets` aliasing, version
pinning, "promote to prod".

### 18. Step debugging with breakpoints — Debug mode (fase 8.3)

**Goal:** inspect the resolved input node by node before it runs.

**Steps:**
1. **🐞 Debug** turns on the mode; click a node's dot to set a **breakpoint**.
2. **Start debug** — the run is created **`paused`**, before any node (`POST /{id}/run` with
   `debug:true`).
3. **⏭ Step** runs the next node and pauses again; **▶ Continue** goes to the next
   breakpoint; **⏹ Stop** cancels (`POST /runs/{id}/debug` with
   `{command, breakpoints?, input?}`).
4. The pending node is purple and the debug bar shows its **resolved input**; the optional
   `input` field simulates that input (edit-the-pin).

**Demonstrates:** debugging built on the resume machinery (each command resumes from the
checkpoint, runs one node, re-pauses); sessions paused past
`GRAPH_WORKFLOW_DEBUG_MAX_PAUSE` (default 1 h) are cancelled.

### 19. The workflow becomes a tool — publish as tool + `chat` trigger (fase 9)

**Goal:** make a workflow callable from `llm.agent`, from chat, and from external MCP
clients.

**As a tool (9.1):** give the workflow an **input contract** (run panel → *Contracts*), tick
**Publish as tool** and **activate** it. It becomes `workflow__<id>`, invocable from other
workflows' `llm.agent`/`tool.*` nodes and from chat; every invocation is a normal run
(metrics + audit). Depth cap `GRAPH_WORKFLOW_TOOL_MAX_DEPTH` (default 3).

**As a chatbot (9.3):**
- **Graph:** `chat → llm.completion → chat.reply`
- `reply` (`chat.reply`) — `text`: `={{ $node.<llm>.output.content }}`.
- Call: `POST /v1/graph-workflows/{id}/chat` with `{ "message": "hi", "session_id": "s1" }`.
  The graph receives `$trigger = {session_id, message, history}` and the session persists
  across turns (purged after `GRAPH_WORKFLOW_CHAT_SESSION_TTL`).

**Over MCP (9.2):** the same workflow is reachable from Claude Desktop/IDE via
`POST /v1/graph-workflows/mcp` (JSON-RPC 2.0: `initialize` / `tools/list` / `tools/call`).

**Demonstrates:** workflow-as-tool with anti-recursion, `chat` trigger + `chat.reply` with
session state, the product MCP server.

### 20. Scheduling, SLA and navigator (fase 17)

Operate dozens of workflows without babysitting them. All configured on the workflow via
`PATCH /v1/graph-workflows/{id}`:

- **Calendars & windows (17.1):** put a timezone on the `schedule` trigger (`"tz":
  "Europe/Rome"`) so each schedule fires in its own zone. Skip holidays with
  `"skip_dates": ["2026-12-25"]` (on the schedule or the workflow). Add blackout windows on
  the workflow: `blackout = {"windows": [{"start":"01:00","end":"02:30","days":[0,1,2,3,4]}],
  "on_conflict":"defer"}` — a run due during the nightly deploy is `skip`ped (advance to the
  next beat) or `defer`red (retry until the window clears). An `end <= start` wraps past
  midnight.
- **SLA monitors (17.2):** `sla = {"max_duration_s":120, "missed_grace_s":900, "channels":["inapp"]}`.
  You get a one-time alert when a run overruns `max_duration_s`, or when an enabled schedule is
  overdue past `missed_grace_s` (the run never started — what the `error` trigger can't see).
- **Navigator (17.3):** `folder`, `tags` and `archived` on workflows.
  `GET /search?q=slack&tag=billing&folder=finance&include_archived=false` runs full-text over
  name, description **and node contents**; `GET /folders` lists the folder tree.
- **Run comparison (17.4):** `GET /runs/compare?a=<run>&b=<run>` — per-node status/duration/output
  of two runs and the **first divergent node** ("why did it work yesterday?").
- **Notification digest (17.5):** `notify = {"digest": {"enabled":true, "interval_s":86400,
  "channel":"inapp"}}` — one daily summary (counts by outcome) instead of a message per run;
  `error`/`waiting` alerting stays immediate.

**Example:** the curated **Nightly report with blackout & digest** template ships the graph;
apply the settings above to complete it.

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
POST   /v1/graph-workflows/{id}/run        { payload, start_node_id?, environment? }
POST   /v1/graph-workflows/{id}/nodes/{nid}/test   { input?, node? }   (single-node test, fase 3.1)
GET    /v1/graph-workflows/runs         ?status=&workflow_id=&limit=   (profile-wide registry)
POST   /v1/graph-workflows/runs/{rid}/cancel   (stop a queued/pending/running/waiting run)
POST   /v1/graph-workflows/runs/{rid}/replay   (re-run with this run's trigger payload)
POST   /v1/graph-workflows/runs/{rid}/retry    (relaunch a FAILED run from its failed node — fase 7.1)
GET    /v1/graph-workflows/{id}/stats/nodes    (per-node health metrics — fase 7.4)
GET    /v1/graph-workflows/{id}/audit          (per-workflow audit trail — fase 7.3)
POST   /v1/graph-workflows/{id}/environments/{env}/promote   { version? }   (pin a version — fase 7.2)
GET    /v1/graph-workflows/approvals        ?status=&run_id=&kind=   (human-in-the-loop requests: approval|input|event, fase 4.4/10)
POST   /v1/graph-workflows/approvals/{aid}/decision   { approved, comment? }
POST   /v1/graph-workflows/approvals/{aid}/submit     { data, comment? }   (human.input form — fase 10.1)
POST   /v1/graph-workflows/events/{correlation_id}    { payload }   (deliver a wait.event — fase 10.2)
GET    /v1/graph-workflows/stats            (per-workflow metrics: runs, success rate, tokens — fase 5.1)
POST   /v1/graph-workflows/import           (portable snapshot → workflow + validation warnings — fase 5.2)
POST   /v1/graph-workflows/generate         { prompt, model?, failover_chain? } → validated draft graph, NOT saved (fase 5.3)
POST   /v1/graph-workflows/generate/stream  (same, streaming: `log` SSE events, then `done`/`error`)
GET    /v1/workspaces/{ws}/workflows        (workflows shared into a workspace — fase 5.2)
POST   /v1/workspaces/{ws}/workflows        { workflow_id, role? }   (share; role = viewer|editor|approver — fase 7.3)
DELETE /v1/workspaces/{ws}/workflows/{wid}  (unshare)
POST   /v1/workspaces/{ws}/workflows/{wid}/import   (copy a shared workflow into my profile)
POST   /v1/workspaces/{ws}/workflows/{wid}/run      (launch a shared workflow; share role editor+ — fase 7.3)
GET    /v1/graph-workflows/{id}/runs
GET    /v1/graph-workflows/{id}/node-outputs   (latest persisted output per node, all past runs)
GET    /v1/graph-workflows/{id}/export         (portable JSON snapshot)
GET    /v1/graph-workflows/{id}/versions
POST   /v1/graph-workflows/{id}/versions/{v}/restore
POST   /v1/graph-workflows/{id}/triggers   { type, config?, enabled? }
DELETE /v1/graph-workflows/triggers/{tid}
GET    /v1/graph-workflows/runs/{rid}      (+ node_runs)
GET    /v1/graph-workflows/runs/{rid}/stream   (SSE live view)
GET    /v1/graph-workflows/tools           (workflows published as callable tools — fase 9.1)
POST   /v1/graph-workflows/mcp             (product MCP server, JSON-RPC 2.0 — fase 9.2)
POST   /v1/graph-workflows/{id}/chat       { message, session_id? } → { session_id, reply, run_id } (fase 9.3)
POST   /v1/graph-workflows/openapi/import  { spec?|url?, path_prefix? } → http.request node drafts (fase 9.4)
GET|POST /v1/graph-workflows/{id}/test-cases   · PUT|DELETE .../test-cases/{cid}   (saved regression tests — fase 11.1)
POST   /v1/graph-workflows/{id}/test-cases/run  (run every saved test case — fase 11.1)
POST   /v1/graph-workflows/{id}/dry-run    { payload }  (simulate the graph, external nodes mocked — fase 11.2)
GET    /v1/graph-workflows/{id}/cost-estimate  (tokens/month projection — fase 11.3)
GET|PUT /v1/graph-workflows/budget         { token_budget_month?, run_budget_month? }  (profile-wide cap — fase 12.1)
GET    /v1/graph-workflows/{id}/budget     (this workflow's usage/status + the profile-wide one — fase 12.1)
POST   /v1/graph-workflows/runners         { name, labels, allowed_node_types } → { id, token } (one-time — fase 14.1)
GET    /v1/graph-workflows/runners         (this profile's runners — online/offline, labels, version — fase 14.1)
DELETE /v1/graph-workflows/runners/{rid}   (revoke a runner's token — fase 14.1)
POST   /v1/wf/hooks/{token}                (public webhook receiver)
POST   /v1/wf/runners/heartbeat            (public, X-Runner-Token — fase 14.1)
GET    /v1/wf/runners/jobs/next            (public, X-Runner-Token — long-poll for a job — fase 14.1)
POST   /v1/wf/runners/jobs/{jid}/result    (public, X-Runner-Token — { ok, output?, handles?, error?, logs? } — fase 14.1)
```

Settings: `GRAPH_WORKFLOW_SCHEDULER_ENABLED` (default on) toggles the schedule poll loop;
`GRAPH_WORKFLOW_MAX_NODES` bounds a single graph; `GRAPH_WORKFLOW_FILES_DIR` is the
workspace storage root for the `file.*` / sqlite `db.query` nodes (fase 4.2);
`GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT` caps how long a `human.approval`/`human.input`/
`wait.event` node may wait (fase 4.4/10, default 7 days). Fase 9: `GRAPH_WORKFLOW_TOOL_MAX_DEPTH` caps the
tool→workflow→tool chain depth (default 3); `GRAPH_WORKFLOW_CHAT_SESSION_TTL` /
`GRAPH_WORKFLOW_CHAT_HISTORY_MAX_TURNS` govern `chat` session retention and history
length; `GRAPH_WORKFLOW_OPENAPI_MAX_OPERATIONS` caps an OpenAPI import. Fase 12:
`GRAPH_WORKFLOW_BUDGET_WARN_PCT` (default 0.8) is the usage fraction that raises the
soft-budget-warning notification; `GRAPH_WORKFLOW_RUNS_RETENTION_DAYS` (default 0 = keep
forever) is the instance-wide run-retention default a workflow's own setting overrides.

## Phase 19 — Custom Node SDK

Extend the palette yourself. A **custom node** is a package with a `node.json`
**manifest** (`type` — always `custom.<name>`, `name`, `category`, `params`/`outputs`
JSON Schemas, `handles`, `secrets`, `permissions`, `kind`) in one of two tiers:

- **declarative** — no code: a parameterised `http.request` template with
  `{{param.x}}` / `{{input}}` placeholders. Safe by construction; retry, rate-limit
  and pins apply exactly like a curated connector.
- **python** — a module defining `run(params, input, ctx)`, **always** executed in
  the sandboxed subprocess (no network, CPU/memory/time caps). `ctx` exposes only
  the declared secrets (`ctx.secrets`) and `ctx.log` — never the vault.

Uploaded packages are versioned (highest version is current); an enabled node
appears in the palette badged *custom*. Deleting a type is blocked while any
workflow still uses it. Optional HMAC **signing** can be required per instance.
Author with the CLI: `sibyl-wf node init|test|pack|push`.

```
GET/POST /v1/graph-workflows/custom-nodes            (list / install)
GET      /v1/graph-workflows/custom-nodes/{type}     (detail, with code)
GET/POST /v1/graph-workflows/custom-nodes/{type}/versions
PATCH    /v1/graph-workflows/custom-nodes/{type}     ({ enabled })
DELETE   /v1/graph-workflows/custom-nodes/{type}     (409 + dependents if in use)
```

Settings: `GRAPH_WORKFLOW_CUSTOM_NODES_DIR`, `GRAPH_WORKFLOW_REQUIRE_SIGNED_NODES`,
`GRAPH_WORKFLOW_NODE_SIGNING_KEY`.

## Phase 20 — Telegram as a workflow channel

Telegram becomes a **bidirectional** channel, not just a notification sink:

- **`telegram` trigger + `/run` launcher** — bind a bot command (`/report`) to a
  workflow, or launch any active workflow from chat with `/run`. `$trigger =
  {chat_id, thread_id, user, text, command, args, launched_via, file?}`; the run's
  terminal `chat.reply`/`telegram.*` output returns to the chat.
- **`telegram.send` / `sendMedia` / `editMessage` / `deleteMessage`** — talk to any
  chat (`chat_id` defaults to `$trigger.chat_id`). Off Telegram they no-op cleanly.
- **`telegram.ask`** — present inline buttons, suspend the run (reusing `wait.event`
  correlation), resume with the tapped value on `main` (timeout → `timeout`).
- **Inbound media** — a document/photo on a `telegram` trigger is fetched into the
  workspace storage and exposed on `$trigger.file` for `file.*` / `doc.convert` /
  `kb.search` (size-capped by `GRAPH_WORKFLOW_TELEGRAM_MAX_FILE_MB`).
- **Bot bindings** — `GET/POST/DELETE /v1/graph-workflows/telegram-bindings`
  (per-profile command collisions rejected); bound commands are published via
  `setMyCommands` on boot.
