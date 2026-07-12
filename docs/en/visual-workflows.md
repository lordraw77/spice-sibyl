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

## Node types

| Category | Nodes |
|----------|-------|
| **Trigger** | `manual`, `schedule`, `webhook`, `event` |
| **Action** | `tool.<name>` — any **built-in** tool (RSS, read_url, weather, kb_search, http_request, python_exec…) · `http.request` (generic HTTP call) · `subworkflow` (run another workflow inline) |
| **MCP & custom** | every **discovered MCP server tool** (`tool.mcp__<server>__<tool>`) and the profile's **custom HTTP tools** (`tool.custom__<name>`) appear as drag-in nodes — no code per tool |
| **Logic** | `if` (true/false branch), `switch` (case branches), `merge` (collect inputs), `for` (for-each over an array), `repeat` (N times), `wait` (pause for N seconds or until a point in time) |
| **Data** | `set` (build an object), `filter` (keep matching array items), `code` (Python sandbox), `aggregate` (reduce an array — sum/avg/min/max/count/concat over a field), `batch` (split an array into fixed-size chunks) |
| **Notify** | `notify.telegram` (linked Telegram chat), `notify.email` (SMTP), `notify.webhook` (Slack/Discord/ntfy/any webhook), `notify.inapp` (web UI bell, zero config) |
| **AI** | `llm.completion` (one provider call), `llm.agent` (the full Phase 18 agent loop, with access to built-in + MCP + custom tools) |

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
  between attempts.
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
  item**, with `$item` and `$index` in scope for that iteration.
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
  `$env` (WF_*-prefixed env vars), `$now`. Functions include `default`, `upper`, `lower`,
  `trim`, `len`, `join`, `slice`, `first`, `last`, `get`, `keys`, `values`, `round`, …

- `=py: …` — an **escape hatch** into the `python_exec` sandbox for real logic
  (list comprehensions, etc.). `ctx`, `input`, `node`, `trigger` are available; the last
  expression (or a `result` variable) becomes the value.

Anything not starting with `=` is a plain literal — with one forgiving exception: a bare
`{{ … }}` (without the leading `=`) is such a common slip that it resolves exactly like
`={{ … }}`.

> **Unwired nodes don't run** — only *trigger* nodes are entry points. A node dropped on
> the canvas but not connected to the flow is recorded as `skipped` at run time instead
> of firing on its own.

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

Both **schedule** and **event** triggers track a consecutive-failure streak
(`fail_count`/`last_error` on the trigger): after
`GRAPH_WORKFLOW_TRIGGER_MAX_FAILURES` (default 5) failures in a row the trigger
auto-disables and an in-app notification is raised so a broken trigger doesn't fail
silently forever. Re-enabling a trigger (`POST /triggers/{tid}/enable`) clears the streak.

## Versioning & runs

Every save snapshots an immutable version; you can list versions and roll back. Each run
stores the executed graph, the resolved run context, and a per-node record (input, output,
error, timing) you can inspect after the fact.

Because every value is persisted, the editor doesn't need a live run to show data:
opening a workflow loads the **latest recorded output of each node across all past runs**
(`GET /{id}/node-outputs`), so clicking an arrow shows the fields and payload that flowed
through it historically — with a "data from a past execution" note and its timestamp.
A fresh run simply replaces those values with live ones.

**Export**: the *Export* button (or `GET /{id}/export`) downloads the workflow as a
portable JSON snapshot (`{ kind, schema_version, name, description, graph, … }`); the same
body is re-importable via `POST /v1/graph-workflows`.

**Import**: the 📥 button next to **New** (top of the workflow list) picks a
`.workflow.json` file from disk — the exact file **Export** produces — and creates a new
workflow from it, opened immediately for editing. It reads only `name`, `description` and
`graph`; the export-only fields (`kind`, `schema_version`, `exported_at`, …) are accepted
and ignored, so any file previously downloaded via Export (from this instance or another
one) round-trips cleanly. A malformed or non-workflow JSON file is rejected client-side
with an error toast instead of being sent to the server.

## API

Everything the UI does is available under `/v1/graph-workflows` (JWT-protected), so a graph
can be created and run entirely from JSON with no UI:

```
GET    /v1/graph-workflows/node-types
GET    /v1/graph-workflows
POST   /v1/graph-workflows                 { name, description?, graph:{nodes,edges} }
GET    /v1/graph-workflows/{id}
PATCH  /v1/graph-workflows/{id}            { name?, description?, graph?, active? }
DELETE /v1/graph-workflows/{id}
POST   /v1/graph-workflows/{id}/activate | /deactivate
POST   /v1/graph-workflows/{id}/run        { payload }
GET    /v1/graph-workflows/runs         ?status=&workflow_id=&limit=   (profile-wide registry)
POST   /v1/graph-workflows/runs/{rid}/cancel   (stop a pending/running run)
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
`GRAPH_WORKFLOW_MAX_NODES` bounds a single graph.
