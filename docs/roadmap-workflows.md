# Roadmap — Workflow Engine Evolution (post Phase 29)

Starting point: DAG engine v2.2.0 (`/v1/graph-workflows`) with `manual/schedule/webhook/event` triggers, `if/switch/merge/for/repeat/filter/aggregate/batch/set/wait` logic, `llm.completion/llm.agent`, `http.request`, `subworkflow`, `code` nodes, `email/telegram/inapp/webhook` notifications, 4 tool wrappers. Angular visual editor (SVG canvas) + Runs and Schedules pages.

Phases are ordered by dependency: each phase enables or simplifies the following ones. Within a phase, items are independent and parallelizable.

Legend: ✅ = done, ⬜ = to do.

---

## Phase 1 — Foundations (refactoring and infrastructure) ✅ COMPLETED (2026-07)

Goal: prepare the ground. No user-visible features, but everything that follows costs less after this phase.

> **Status:** all four items are implemented and tested.
> 1.1 — editor split into 6 standalone components under `frontend/src/app/features/workflows/editor/` (graph-canvas, node-palette, editor-toolbar, node-inspector, edge-inspector, run-panel); the page component is an orchestrator.
> 1.2 — `/graph-workflows/:id` shell with Editor | Runs | Schedules tabs scoped to the workflow (`workflow-shell.component`); the global pages remain for the cross-workflow view.
> 1.3 — per-workflow `$vars` (`variables_json` column, PATCH `variables`, editor in the run panel) and profile `$secrets` encrypted with Fernet via `VAULT_SECRET_KEY` (`workflow_secrets` table, GET/PUT/DELETE `/secrets` endpoints, never returned in cleartext, masked in previews, excluded from export).
> 1.4 — backend versioning already existed (snapshot on save, restore); added the UI: **Versions** section in the run panel with list and Restore.
> Docs updated in 5 languages + frontend-overview + examples + `.env.example`; 6 new backend tests (63 engine tests total, green).

### ✅ 1.1 Graph editor UI refactoring
Split `graph-workflow-page.component` (previously ~1,300 lines TS + ~500 HTML) into standalone components under `frontend/src/app/features/workflows/editor/`:

| Component | Responsibility |
|---|---|
| `graph-canvas.component` | SVG only: node/edge rendering, pan/zoom, drag, selection. Communicates via `@Input`/`@Output` (`nodeMoved`, `edgeCreated`, `nodeSelected`), never touches services |
| `node-palette.component` | Draggable node catalog, fed by `node_catalog()` |
| `node-inspector.component` | Properties panel for the selected node |
| `expression-input.component` | Reusable input with `$node`/`$item`/`$index` expression preview (uses `preview_expression()`) |
| `editor-toolbar.component` | Save, run, activate/deactivate, zoom, layout |

The page component remains a thin orchestrator (~250 lines): graph state, component wiring.

**Why first**: run overlay (3.3), copy/paste/undo (3.4), minimap (3.5) become trivial on an isolated canvas, impractical on the monolith.

### ✅ 1.2 Per-workflow navigation shell
Route `workflows/:id` with **Editor | Runs | Schedules** tabs scoped to the single workflow. The global Runs/Schedules pages remain for the cross-workflow view. Eliminates the "global list → filter by hand" loop.

### ✅ 1.3 Workflow-level variables and credentials
- `$vars` defined on the workflow (editable from the UI) and global workspace `$env`.
- Encrypted secrets (at-rest) referenceable as `$secrets.<name>` from `http.request`, headers, body — never serialized into run logs nor exports.
- Extension of `expression_resolver.py` + dedicated table + inspector section.

**Why in phase 1**: db/file nodes (4.2), export/import (5.2) and templates (3.6) assume credentials do not live hardcoded in the graph.

### ✅ 1.4 Workflow versioning
Immutable snapshot of the definition on every save; every run records `workflow_version_id`. UI: version list, diff (JSON), rollback.

**Why in phase 1**: from here on, every feature changes the graph schema — having versions and rollback makes later changes safe; runs become reproducible.

---

## Phase 2 — Engine reliability ✅ COMPLETED (2026-07-15)

Goal: a run must not die on a transient error, and the system must withstand real load.

> **Status:** all five items are implemented and tested (8 new tests, 71 green on the engine).
> 2.1 — `retry`/`backoff`/`timeoutMs` already existed; added `backoffStrategy` (fixed | exponential, pause `backoff × 2^attempt` capped at 60 s), the Backoff/Strategy fields in the inspector, and **catalog defaults** applied on drop (`http.request`: 2 exponential retries at 2 s + 60 s timeout; `llm.*`: 1 retry + 120/300 s timeout).
> 2.2 — already implemented in Phase 30: DAG waves run with `asyncio.gather` under the `GRAPH_WORKFLOW_MAX_CONCURRENT_NODES` semaphore (default 8); `merge` nodes synchronize joins.
> 2.3 — `max_concurrent_runs` column on the workflow (0 = unlimited, **Execution** section in the run panel): runs over the threshold are born in `queued` state (payload parked in `context_json`) and promoted FIFO by `_maybe_start_queued()` at run end and at startup. Cancellable from the queue; `subworkflow` children bypass the queue.
> 2.4 — the per-wave checkpoint now includes each node's **active handles**; `resume_interrupted_runs()` at startup (`GRAPH_WORKFLOW_RESUME_ON_STARTUP` flag) resumes `running`/`pending` runs from the checkpoint re-executing only the missing subgraph and closes orphan node runs as "interrupted by restart".
> 2.5 — `error` trigger (+ `error` trigger node in the catalog): fires on another run's failure with `$trigger = {workflow_id, workflow_name, run_id, error, failed_node}`; `config.workflow_id` filter (empty/`*` = all), anti-loop guards (never self, never cascading from `error` runs). Curated example `error-alert-hub`.
> Docs updated in 5 languages + examples + frontend-overview + `.env.example`; `queued` state visible/filterable in the Runs view.

### ✅ 2.1 Per-node retry policy
`retries`, `backoff` (fixed/exponential), `timeout` fields in each node's config, handled in `_run_node()`. UI in the inspector. Sensible defaults for `http.request` and `llm.*`. The `onError` branch fires only after retries are exhausted.

### ✅ 2.2 Parallel branch execution
Independent DAG branches executed with `asyncio.gather` in `_execute()` instead of topological sequence. Per-run parallelism limit (default e.g. 5). `merge` nodes already synchronize join points.

### ✅ 2.3 Per-workflow concurrency limit and queue
`max_concurrent_runs` per workflow; runs over the threshold go into `queued` state and start when a slot frees up. Prevents a dense schedule or a webhook burst from saturating the backend.

### ✅ 2.4 Resumable runs / checkpoints
Persist completed nodes' output during the run (not only at run end). After a crash/restart, `running` runs resume from the uncompleted nodes. Technical prerequisite for `human.approval` (4.4) and long `wait`s.

### ✅ 2.5 Error trigger
New `on_workflow_error` trigger: a workflow starts when another one (or any, with a filter) fails, receiving `{workflow_id, run_id, error, failed_node}`. Enables centralized alerting reusing the existing notify nodes. Hooks into `_maybe_alert_recurring_failures()`/`dispatch_event()`.

---

## Phase 3 — Developer experience in the editor ✅ COMPLETED (2026-07-16)

Goal: building and debugging a workflow must be as fast as in n8n. Depends on the 1.1 refactoring.

> **Status:** all six items are implemented and tested (Phase 34; 6 new backend tests, 77 green on the engine).
> 3.1 — `POST /{id}/nodes/{node_id}/test` + `engine.test_node()`: executes the single node with the current parameters (even unsaved, passed in the body as `node`) and optional mock input; `$node` context from pins/history, `$trigger` from the last run; no run recorded; result `{ok, output, handles, duration_ms}` shown inline in the inspector (⚡ Test node) and projected onto the canvas.
> 3.2 — `pinnedOutput` field on `GraphNode` (saved with the graph, versioned, exported): node tests, partial runs and `preview-expression` resolve `$node.<id>.output` from the pin instead of history; production runs ignore it. UI: 📌 Pin section in the inspector (pin last output / editable JSON / remove) + canvas badge.
> 3.3 — the run overlay already existed (Phase 30: nodes colored by state via SSE + poll, live output, re-attach to external runs); added the **Last execution** section in the inspector (state/output/error of the selected node).
> 3.4 — copy/paste/undo/redo existed (Phase 30.c); added multi-selection (shift+click, `Ctrl+A`), group drag, copy/paste of the selection with internal edges (ids remapped), `Del`/`Backspace`.
> 3.5 — pan (background drag) + zoom (wheel, cursor-anchored), clickable/draggable minimap with viewport (double click = fit), **Rearrange** (longest-path layered auto-layout, undoable) and **⛶ fit view** in the toolbar.
> 3.6 — the examples panel is a template gallery: `graph-preview.component` (read-only mini-SVG of the graph) on each card + category filter.
> Docs updated in 5 languages + frontend-overview + developer-guide.

### ✅ 3.1 Single-node test execution
"Run this node" from the inspector: executes the node with the current (or mock) inputs and shows the output inline on the canvas. Endpoint `POST /graph-workflows/{id}/nodes/{node_id}/test`.

### ✅ 3.2 Data pin/mock
Freeze a node's output (e.g. a real webhook payload) and reuse it in test executions of downstream nodes while developing the rest of the graph. Pins saved with the workflow, ignored in production runs.

### ✅ 3.3 Run overlay on the canvas
Replay of a run on the graph: nodes colored by state (success/error/skipped/running), click on a node → input/output/duration. The runs page already exists; what's missing is the projection onto the canvas. Live via WebSocket/polling for in-progress runs.

### ✅ 3.4 Copy/paste, duplication, undo/redo, multi-selection
Standard editing operations on the canvas. Undo/redo as a command stack on the graph model.

### ✅ 3.5 Minimap and auto-layout
Minimap for large graphs; auto-layout with dagre/elk ("rearrange graph" in the toolbar).

### ✅ 3.6 Template gallery
Expose the workflows from `graph_workflow_examples.py` as a "create from template" gallery in the UI, with graph preview. Depends on 1.3 for templates that require credentials.

---

## Phase 4 — New nodes and capabilities ✅ COMPLETED (2026-07-16)

Goal: broaden the covered use cases. Every node benefits from retry (2.1) and per-node testing (3.1).

> **Status:** implemented and tested (Phase 35; 14 new backend tests, engine suite green).
> 4.1 — `llm.classify` node (output `{category, confidence}`, out-of-list category ⇒ error, so retry/onError apply) and `llm.extract` (JSON Schema in the inspector, top-level `required` verified, output `{data}`); they share `llm.completion`'s model picker, failover chain, response cache and retry presets; parsing tolerates code fences and prose around the JSON.
> 4.2 — `db.query` (sqlite in workspace storage; postgres via `dsn` from `$secrets` with optional asyncpg; parameterized queries, output `{rows, count, rowcount}` max 1000 rows) and `file.read`/`file.write`/`file.parse` (auto/json/csv/lines formats, max 10 MB). Sandbox: every path is resolved inside `GRAPH_WORKFLOW_FILES_DIR` (default `data/workflow_files`), traversal and absolute paths rejected.
> 4.3 — **already implemented** by previous phases: `node_catalog()` generates `tool.*` nodes dynamically from `TOOL_DEFINITIONS` (parameter schema included) plus discovered MCP tools and the profile's custom tools — no manual wrappers left.
> 4.4 — `human.approval` node: the run goes into **`waiting`** state (new state, purple chip in the Runs view), row in `workflow_approvals`, in-app notification (+ optional Telegram), poll-based wait until decision or `timeout` (`onTimeout: reject|fail`, cap `GRAPH_WORKFLOW_APPROVAL_MAX_TIMEOUT` = 7 days); `approved`/`rejected` output handles. API `GET /approvals` + `POST /approvals/{id}/decision`; approve/reject UI in the runs page. Survives restarts (resume 2.4 re-attaches to the pending request); cancel closes the request as `cancelled`; a `waiting` run does not occupy a `max_concurrent_runs` slot; single-node test (3.1) rejects the node.
> Curated examples `approval-gate-deploy` and `ticket-triage-classify`; docs updated in 5 languages + developer-guide + frontend-overview + `.env.example`.

### ✅ 4.1 `llm.classify` / `llm.extract`
Structured output guaranteed by a JSON schema defined in the inspector (provider structured output / tool-use). Much more robust than free-form prompts + parsing in a `code` node.

### ✅ 4.2 Database and file nodes
- `db.query`: SQLite/Postgres, connection from `$secrets` (1.3), parameterized queries, output `{rows, count}`.
- `file.read` / `file.write` / `file.parse` (CSV, JSON, text) on workspace storage.

### ✅ 4.3 Full tool registry exposure
Generate `tool.*` nodes dynamically from `registry.py` in `node_catalog()` (parameter schema included) instead of the previous 4 manual wrappers. Every newly registered tool automatically becomes a node.

### ✅ 4.4 `human.approval` node
The run suspends (requires checkpoint 2.4), sends a notification with a link (existing notify channels), resumes on approval/rejection with dedicated branches and configurable timeout. Unlocks enterprise approval use cases.

---

## Phase 5 — Platform and product ✅ COMPLETED (2026-07-16)

Goal: from internal engine to product.

> **Status:** implemented and tested (Phase 36; 8 new backend tests, engine suite green).
> 5.1 — `GET /v1/graph-workflows/stats`: per-workflow aggregates (runs by outcome, success rate over terminal runs, average duration, total LLM tokens summed via `json_extract` from the `_usage` key of `llm.*` nodes). UI: dashboard strip in the Runs view (follows the workflow filter) + total run tokens in the detail. No invented costs: tokens only (no per-model price list in the repo).
> 5.2 — export includes `secrets` (names of referenced `$secrets.<name>`, never the values); dedicated `POST /import` with schema validation/node limit (400) and non-blocking warnings (unknown node types, broken edges, `$secrets` missing from the profile) shown as toasts; cross-workspace sharing on the Phase 20 pattern (`workspace_workflows` table, `GET/POST /{ws}/workflows`, `DELETE /{ws}/workflows/{wid}`, `POST /{ws}/workflows/{wid}/import` = "… (shared)" copy into the member's profile with the same warnings).
> 5.3 — `POST /generate` ({prompt, model?, failover_chain?}): the node catalog (types/outputs/parameters) serves as LLM context; the `{name, description, graph}` response is validated and **normalized** (unknown types and broken edges discarded with warnings, `manual` trigger prepended if missing, layered auto-layout for nodes without a position) and returns as an **unsaved draft**; UI: 🪄 "describe what you want" dialog in the editor with **model picker + failover chain** and **live progress log** via `POST /generate/stream` (SSE `log` events per stage — catalog, call, response, validation, layout — then `done`/`error`), the draft opens for review.
> UX extras (same date): template gallery as a large centered modal (more detailed cards: bigger preview, category, flow chain, counts) and collapsible workflow list (persisted preference) in favor of the palette.
> Docs updated in 5 languages + developer-guide + frontend-overview; i18n in 5 languages.

### ✅ 5.1 Metrics and observability
Per-node duration, LLM tokens/cost per run (hooked into the provider layer), success rate per workflow. Aggregated dashboard + costs column in the runs page.

**Extended in Phase 39 (fase 7.2):** `GET /stats` accepts an optional `?environment=<name>` query param that scopes every aggregate (runs, success rate, avg duration, tokens) to runs executed in that named environment, so `prod` health can be compared against the unfiltered (all-environments) totals without a separate call per run. A workflow with zero runs in the requested environment still appears with `runs: 0` rather than being omitted.

### ✅ 5.2 Export/import and sharing
JSON export of the workflow (without secrets, with placeholders to remap on import), import with schema validation, cross-workspace sharing. Depends on 1.3 and 1.4.

**Extended in Phase 39 (fase 7.2/7.3):** `GET /{id}/export`, `POST /import` and `POST /{ws}/workflows/{wid}/import` now also carry the workflow's `environments` (fase 7.2) — `$vars` overlays and `$secrets` **alias** bindings only, following the same "names travel, values never do" rule as the pre-existing `secrets` array; a pinned `version` re-applies only after promoting again in the target environment, since version numbers aren't portable across workflows. `POST /{ws}/workflows` also gained an optional `role` (fase 7.3) alongside `workflow_id`.

### ✅ 5.3 LLM-generated workflows
"Describe what you want" → the backend generates the graph JSON using the node schema as context, validates it and opens it in the editor as a draft. Reuses the provider layer + `node_catalog()` (enriched by 4.3). Flagship product feature.

---

## Phase 6 — Engine extension (triggers, loops, composition) ✅ COMPLETED (2026-07-19)

Goal: cover the integration patterns that today require workarounds (manual polling, recursive subworkflows, HTTP wrappers). Everything reuses existing infrastructure: checkpoints (2.4), queue (2.3), dynamic catalog (4.3).

> **Status:** implemented and tested as **Phase 38, v3.1.0** (numbered 38 in the main roadmap because 37 was reserved for pluggable persistence). `success` trigger + `crons:` multi-cron recurrence; poll-based `file.watch` / `email.inbound` in the schedule loop (state in the trigger config, attachments to workspace storage); `while` loop with per-iteration condition re-resolution and `GRAPH_WORKFLOW_WHILE_MAX_ITERATIONS` cap; `input_schema`/`output_schema` contracts (columns + Contracts panel + export/import) with a dependency-free schema-subset validator and typed `workflow.<id>` catalog nodes; `kb.search` over `rag_service.retrieve` with `document_ids` filter; per-host sliding-window rate limiter on `http.request` (`maxRequestsPerMinute` + `GRAPH_WORKFLOW_RATE_LIMITS`), wait surfaced as `rate_limited_s`. 21 tests in `test_phase38.py`; schedules/run-panel UI + i18n and docs in 5 languages.

### ✅ 6.1 `success` trigger and multiple crons
- `on_workflow_success` trigger: a workflow starts on the **successful** completion of another one (or any, with a `workflow_id` filter like the `error` trigger 2.5), receiving `$trigger = {workflow_id, workflow_name, run_id, output}`. Enables "A then B" pipelines without subworkflows.
- Schedules with **multiple cron expressions** per trigger (list instead of a single one), for mixed timetables (e.g. weekdays 9-18 + reduced weekends) without duplicating the workflow.
- Same anti-loop guards as the `error` trigger (never self, never cascading).

### ✅ 6.2 `file.watch` and `email.inbound` triggers
- `file.watch`: fires on file creation/modification in a subfolder of `GRAPH_WORKFLOW_FILES_DIR` (glob pattern in config); `$trigger = {path, event, size}`. Poll-based implementation (reuses the schedule loop), no inotify dependency.
- `email.inbound`: IMAP poll with credentials from `$secrets`, sender/subject filter; `$trigger = {from, subject, body, attachments}` with attachments saved to workspace storage (readable with `file.read` 4.2).

### ✅ 6.3 `while` loop
Logic node `while` with a condition (same expression syntax as `if`) and a **mandatory iteration cap** (`maxIterations`, default 100, limit `GRAPH_WORKFLOW_WHILE_MAX_ITERATIONS`). In the body, `$item`/`$index` apply as for `for`/`repeat` (see existing loop conventions). Covers polling of async APIs and pagination without subworkflow recursion.

### ✅ 6.4 Sub-workflows with contracts
Input/output schema declarable on the workflow (JSON Schema, section in the run panel): the `subworkflow` node validates the input on call and the output on return; the catalog exposes "callable" workflows as typed nodes. LLM generation (5.3) receives the contracts in its context and can compose existing workflows instead of regenerating them.

### ✅ 6.5 `kb.search` node (knowledge base bridge)
Dedicated node that queries the Phase 28 KB (semantic search over workspace documents): config `query` (expression), `top_k`, collection filter; output `{results: [{text, score, source}]}`. Closes the workflow ↔ KB loop: RAG inside workflows without going through a generic `llm.agent`.

### ✅ 6.6 Per-resource rate limiting
Throttling of `http.request` (and HTTP-based `tool.*` nodes) per host: `maxRequestsPerMinute` in the node config or a global per-domain limit (`GRAPH_WORKFLOW_RATE_LIMITS`, host→rpm map). Requests over the threshold wait (they don't fail); the wait time is visible in the node run. Complementary to the per-run queue (2.3), which does not protect external APIs called by parallel runs.

---

## Phase 7 — Operations and governance ✅ COMPLETED (2026-07-20)

Goal: manage workflows as production assets: targeted recovery, environments, traceability, permissions. Depends on versioning (1.4), checkpoints (2.4), sharing (5.2).

> **Status:** implemented and tested as **Phase 39, v3.2.0** (13 new backend tests in `test_phase39.py`).
> 7.1 — replay already existed (Phase 30); added `POST /runs/{id}/retry` (failed runs only): a new run over the origin's graph snapshot, seeded from the checkpoint, re-executing only the failed node's subgraph. Both retry and replay record `origin_run_id`; ↺ Retry button + lineage in the Runs page.
> 7.2 — `environments` map on the workflow ({name: {vars, secrets, version}}): env `vars` overlay `$vars`, `secrets` remap `$secrets` aliases, `version` pins a promoted graph version (`POST /{id}/environments/{env}/promote`). Selectable on manual runs and in schedule/webhook trigger configs; runs record their environment (badge in the Runs view); Environments section in the run panel; environments travel with export/import (aliases only, never values).
> 7.3 — per-workflow audit trail over the existing `audit_log` (`GET /{id}/audit`; activate/deactivate now audited too) and **share roles** on `workspace_workflows` (viewer | editor | approver): editors may launch runs of the shared workflow (`POST /{ws}/workflows/{wid}/run`, executed under the owner's profile), approvers may decide its `human.approval` requests.
> 7.4 — `GET /{id}/stats/nodes`: per-node executions by outcome, error rate, avg/p50/p95 duration, LLM tokens, unhealthiest first; **Health** tab in the workflow shell (metrics + audit trail).
> 7.5 — `human.approval` Telegram notifications carry inline ✅ Approve / ❌ Reject buttons; the bot callback checks the chat ↔ profile link and settles the request like the decision endpoint (first writer wins).
> Docs updated in 5 languages + i18n; CHANGELOG [3.2.0].

### ✅ 7.1 Replay and "retry from here"
- `POST /runs/{id}/retry`: relaunches a failed run **from the failed node**, reusing the outputs already computed from the checkpoint (same mechanics as `resume_interrupted_runs()` 2.4, but on explicit request).
- `POST /runs/{id}/replay`: new run with the same original `$trigger` (payload from `context_json`).
- UI: buttons in the runs page and in the run overlay; the derived run records a reference to the origin run.

### ✅ 7.2 Per-workflow environments (dev/prod)
Named sets of `$vars` + `$secrets` bindings (e.g. `dev`, `prod`) selectable at run time and on schedules/triggers; "Promote to prod" = publish a specific version (1.4) as active for the prod environment while the editor keeps working on dev. Runs record the environment and `workflow_version_id`. No graph duplication.

### ✅ 7.3 Audit log and roles
- Audit: who created/modified/activated/executed/approved what and when (`workflow_audit` table, append-only), browsable per workflow and per workspace.
- Workspace roles on shared workflows (5.2): `viewer` (reads, sees runs), `editor` (modifies, executes), `approver` (decides `human.approval`s). The owner remains implicit admin.

### ✅ 7.4 Per-node metrics
Extension of the 5.1 stats with aggregates **per node type and per node** over time: p50/p95 duration, error rate, retry rate, tokens for `llm.*` nodes. UI: "Health" tab in the workflow shell with the worst nodes highlighted; helps retry/timeout tuning (2.1).

### ✅ 7.5 Approval via Telegram
`human.approval` notifications (4.4) on Telegram include inline Approve/Reject buttons (the bot callback already exists in the backend): the decision via bot is equivalent to `POST /approvals/{id}/decision`, with verification that the Telegram user is linked to the approver profile. Deep-link to the runs page in the in-app message.

---

## Phase 8 — Advanced editor ✅ COMPLETED (2026-07-20)

Goal: readability and debugging of large graphs. Depends on versioning UI (1.4), node test (3.1), pins (3.2), run overlay (3.3).

> **Status:** implemented and tested as **Phase 40, v3.3.0** (13 new backend tests in `test_phase40.py`).
> 8.1 — `GET /{id}/versions/{a}/diff/{b}` computes a structural diff (added / removed / changed / unchanged nodes + edge deltas; node **position is ignored** — moving a node is not a change) and returns the per-node config before/after; the editor paints the current canvas (added green, changed yellow) and lists removed nodes + counts in a diff bar. A **compare** row in the run panel's Versions section drives it (defaults to previous → current).
> 8.2 — `notes` array on `WorkflowGraph` (sticky notes + frames): rendered on the canvas (frames behind everything, notes on top), draggable, double-click to edit (empty text deletes), added from the toolbar. Saved with the graph, versioned and carried by export/import; the engine never reads them (`_execute` only ever iterates `nodes`/`edges`).
> 8.3 — step debugger: `POST /{id}/run` with `debug:true` creates the run in a new **`paused`** status (no node has run); `POST /runs/{id}/debug` advances it — `step` runs the next node then pauses, `continue` runs to the next breakpoint (or the end), `stop` cancels. Breakpoints are set by clicking a node's dot in debug mode; the paused run exposes its `pending_node` and the resolved input; an optional `input` override mocks the next node (edit-the-pin). Built on the fase 2.4 resume machinery (each command re-spawns from the checkpoint). A scheduler sweep cancels sessions left paused past `GRAPH_WORKFLOW_DEBUG_MAX_PAUSE` (default 1 h).
> Docs updated in 5 languages + i18n; CHANGELOG [3.3.0]; `.env.example`.

### ✅ 8.1 Visual diff between versions
Comparison of two versions projected onto the canvas: added nodes (green), removed (red), modified (yellow, with a JSON diff of the config in the panel). Reuses `graph-preview.component` for the side-by-side preview. With 7.2 it becomes "what changes when promoting to prod".

### ✅ 8.2 Notes and frames on the canvas
- Sticky notes (markdown text, color) placeable on the canvas, saved in the graph but ignored by the engine.
- Frames: named rectangles that group nodes (nodes inside move with the frame). Presentation only: no effect on execution.

### ✅ 8.3 Step-by-step debugging
"Step" mode in the run panel: the run stops **before** each node (or only on breakpoints set from the canvas), shows the resolved input, and continues on "Next" / "Continue to next breakpoint". Implemented as a `paused` state on the per-wave checkpoint (2.4); while paused, the pin (3.2) of the next node can be edited. Debug session timeout so runs are not left suspended.

---

## Phase 9 — Workflows as ecosystem tools ✅ COMPLETED (2026-07-20)

Goal: the product multiplier — a workflow is no longer just something that runs on a trigger, but a component invocable from the chat, from agents and from external clients. Depends on subworkflow contracts (6.4).

> **Status:** implemented and tested as **Phase 41, v3.4.0** (13 new backend tests in `test_phase41.py`).
> 9.1 — `expose_as_tool` flag on the workflow: an **active** workflow with an input contract becomes a namespaced `workflow__<id>` tool available to `llm.agent` nodes, other workflows' `tool.*` nodes and the product chat (`workflow_tool_service`, routed by `registry.execute_tool`). Invocation runs the workflow inline as a first-class run (stats/audit apply) and returns its sink output; an anti-recursion **depth guard** (contextvars, `GRAPH_WORKFLOW_TOOL_MAX_DEPTH`) caps the tool→workflow→tool chain. `GET /tools` lists the published tools; a "Publish as a tool" toggle sits in the run panel's Contracts section.
> 9.2 — the product's **workflow MCP server** (`POST /v1/graph-workflows/mcp`): a JSON-RPC 2.0 endpoint (streamable-HTTP transport) that publishes the same `expose_as_tool` workflows to external MCP clients — `initialize` / `tools/list` / `tools/call` / `ping` + `notifications/*` no-ops. A `tools/call` runs the workflow inline (trigger origin `mcp`) and returns its output as MCP `content`; auth is the caller's normal credential (`workflow_mcp_service`).
> 9.3 — `chat` trigger + `chat.reply` node: `POST /{id}/chat` ({session_id?, message}) runs the workflow with `$trigger = {session_id, message, history}` and returns the terminal `chat.reply` node's text; session state persists in `workflow_chat_sessions` (rolling history, `GRAPH_WORKFLOW_CHAT_SESSION_TTL` purge, `…_HISTORY_MAX_TURNS` trim).
> 9.4 — OpenAPI import (`POST /openapi/import`, inline `spec` or `url`): each operation becomes a preconfigured `http.request` node draft (method, URL from the server + path, query params, auth mapped onto `$secrets` placeholders), returned to the editor unsaved (`openapi_import_service`, capped by `GRAPH_WORKFLOW_OPENAPI_MAX_OPERATIONS`).
> Docs updated in 5 languages + developer-guide + `.env.example`; i18n in 5 languages; CHANGELOG [3.4.0].

### ✅ 9.1 Workflow exposed as a tool
An active workflow with an I/O contract (6.4) becomes a profile **custom tool**: invocable by `llm.agent`, by `tool.*` nodes of other workflows and by the product chat. The registry (4.3) exposes it with name, description and JSON Schema of the parameters derived from the contract. Invocation creates a normal run (queue 2.3 and stats 5.1 apply); the result returns to the caller as the tool output. `exposeAsTool` flag on the workflow + anti-recursion guard (maximum depth of the tool→workflow→tool chain).

### ✅ 9.2 Workflow as an MCP tool
Extension of 9.1 outwards: workflows with `exposeAsTool` are also published by the product's MCP server, hence invocable by external MCP clients (Claude Desktop, IDEs). Authentication with the existing API keys; runs record the `mcp` origin.

### ✅ 9.3 `chat` trigger
Workflow used as a chatbot: the trigger is a conversation message, `$trigger = {session_id, message, history}`. Session state persists across messages (dedicated table, configurable TTL); the workflow's reply (terminal `chat.reply` node) returns to the origin channel (chat UI, Telegram, webhook). Correlation by `session_id`, one run per message.

### ✅ 9.4 OpenAPI import
Paste an OpenAPI spec (URL or file) → the catalog generates preconfigured `http.request` nodes per operation (URL, method, parameters with schema, auth mapped onto `$secrets`). Imported operations appear in the palette in a dedicated per-API section. Slashes the cost of integrating any REST service.

---

## Phase 10 — Advanced human-in-the-loop

Goal: beyond approve/reject — structured data collection and waits for external events. Reuses the `waiting` state, the `/approvals` API (4.4) and resume (2.4).

### ✅ 10.1 `human.input` node (form)
Like `human.approval`, but the request contains a **form defined by JSON Schema** (fields, types, required, defaults); the run resumes with the filled data as the node output (`{data}` validated against the schema). UI: form rendering in the runs page and in the in-app notification; timeout and `onTimeout` as for approval. Unlocks "ask the operator for the missing value" flows.

Implemented as Phase 42: the `human.input` node (outputs `submitted`/`timeout`) reuses the Phase 35 `workflow_approvals` row, generalised with a `kind` column (`approval|input|event`), a `schema_json` column for the form's JSON Schema and a `data_json` column for the submission. `POST /graph-workflows/approvals/{id}/submit` validates the submitted `data` against the schema (the existing dependency-free `_validate_json_schema`) before accepting it and waking the run.

### ✅ 10.2 `wait.event` node with correlation
The run suspends until an external event with a **correlation ID** arrives: `POST /graph-workflows/events/{correlation_id}` (authenticated) wakes the run and delivers the payload as the node output. The correlation ID is an expression (e.g. the order ID from the trigger); configurable timeout with an `onTimeout` branch. Covers real async systems: payments, digital signatures, tickets, third-party callbacks. A `waiting` run does not occupy a slot (as in 4.4).

Implemented as Phase 42: the `wait.event` node (outputs `main`/`timeout`) creates a `kind='event'` request keyed by `correlation_id`; `POST /graph-workflows/events/{correlation_id}` (scoped to the caller's profile) delivers the payload into `data_json` and wakes the run, which resumes with the payload as its output. Both nodes share the fase 4.4 poll/resume machinery (`_wait_for_decision`), so they survive a backend restart exactly like `human.approval`.

---

## Phase 11 — Workflow quality and testing

Goal: treat workflows like code — regression tests, dress rehearsals, predictable costs. Reuses pins (3.2), node test (3.1), versioning (1.4).

### ✅ 11.1 Workflow test suites
Test cases saved with the workflow: fixture `$trigger` + assertions on the output of chosen nodes (equality, contains, JSON path, schema). Run on demand ("Run tests" in the toolbar); per-case result (green/red, expected/actual per assertion). External nodes can be replaced by pins for deterministic tests.

Implemented as Phase 43: `workflow_test_cases` (id, workflow_id, name, trigger_payload_json, assertions_json) is a plain CRUD resource under `/{id}/test-cases`. `POST /{id}/test-cases/run` runs every saved case: each executes the workflow with its fixture `$trigger` through the same `_execute` scheduler as a normal run (so it is a first-class, observable `workflow_runs` row), with `_use_pins=True` in the run context — external-effect nodes (`http.request`/`db.query`/`notification.*`/`llm.*`) that carry a fase-3.2 pinned output use it instead of making the real call; nodes without a pin still execute for real, so pins are an opt-in determinism aid, not a requirement. `equals`/`contains`/`json_path`/`schema` assertions are then checked against the actual persisted node outputs (`_validate_json_schema` from fase 6.4 backs `schema`).

### ✅ 11.2 Full dry-run
Simulated execution of the whole graph: `http.request`, `db.query`, `notification.*`/`email.*` and `llm.*` are mocked — they respond from the pin if present, otherwise from a typed placeholder. Final report: path taken, simulated outputs, nodes that *would have had* external effects. To be used before activating a schedule on a new graph.

Implemented as Phase 43: `POST /{id}/dry-run` runs the graph through `_execute` with `_dry_run=True`. The same interception point as 11.1 (`_mock_dispatch`, gating on `_is_external_effect`) now mocks *every* external-effect node unconditionally — a pin when present, else `_dry_run_placeholder(node_type)` (a shape-typed stand-in, e.g. `{status, headers, body}` for `http.request`, `{text, _usage}` for `llm.completion`). Every interception is recorded on the run's checkpointed context as `dry_effects` and returned in the report alongside the execution path and every node's simulated output. The real node executor is never invoked for a mocked node, so a dry-run is safe to run against a new, unreviewed graph.

### ✅ 11.3 Pre-run cost estimate
Given the graph (and the active schedule), a static estimate: number of LLM calls per run × historical average tokens (stats 5.1) × schedule frequency → token/month projection per workflow. Shown in the run panel. No invented price list: tokens only, consistent with 5.1.

Implemented as Phase 43: `GET /{id}/cost-estimate` counts the graph's `llm.*` nodes, sums their historical `tokens_total` from the fase 7.4 per-node stats and divides by the workflow's total run count (fase 5.1) for an average-tokens-per-run figure. Schedule frequency comes from the workflow's enabled `schedule` trigger(s): `reminder_parsing.compute_next_fire` is called twice to derive the recurrence's interval, extrapolated to a 30-day month. Either half missing (no run history, or no active schedule) degrades gracefully — the response's `basis` field always explains what the number does and does not account for.

---

## Phase 12 — Data and budget governance ✅ COMPLETED (2026-07-21)

Goal: guardrails before pushing schedule + LLM into production. Complementary to audit and roles (7.3).

> **Status:** implemented and tested as **Phase 44** (12 new backend tests in `test_phase44.py`).
> 12.1 — `token_budget_month`/`run_budget_month` columns on the workflow plus a profile-wide ("workspace") cap (`profile_budgets` table, `GET/PUT /v1/graph-workflows/budget`): usage is derived on the fly from the fase 5.1 stats sources (`workflow_runs`/`workflow_node_runs`, time-boxed to the current UTC calendar month) rather than duplicated in a counter, so a period "resets" for free. `run_workflow()` checks both caps before spawning; a cap fully reached raises `BudgetExceededError` (a `ValueError`) — a manual/API run is rejected with an explicit 400, and a schedule/event trigger firing is caught by the existing Phase 30.b consecutive-failure counter and auto-disables past the configured threshold, reusing that infrastructure rather than adding a parallel one. Crossing `GRAPH_WORKFLOW_BUDGET_WARN_PCT` (default 0.8) of either cap fires a one-time in-app soft warning per period. `GET /{id}/budget` reports both the workflow's own usage and the profile-wide one it is also gated by. Partial/dev runs and step-debug runs never count against a budget.
> 12.2 — per-workflow `runs_retention_days` overriding the global `GRAPH_WORKFLOW_RUNS_RETENTION_DAYS` default (0 = keep forever); the scheduler sweep purges terminal (completed/failed/cancelled) runs past the cutoff — `queued`/`pending`/`running`/`waiting`/`paused` runs are never touched. **Redaction**: a `redact: string[]` field on `GraphNode` (dotted JSON paths) masks matching leaves as `"***"` in the persisted `workflow_node_runs` output, the live SSE event and a pinned output carried by export/share — the *live run context* keeps the real value so downstream node expressions still resolve it in cleartext. `$secrets` remain never serialized regardless (1.3).
> Docs updated in 5 languages + developer-guide + `.env.example`; editor UI: **Budget & quotas** subsection in the run panel's fase-11 details block, **Redact** field in the node inspector's Advanced section.

### ✅ 12.1 Budgets and quotas
**LLM token** and **run** caps per period (month) at workflow and workspace level: soft warning (in-app notification when crossing the % threshold) and hard stop (runs are born `failed` with an explicit error, schedules get suspended). Counters derived from the 5.1 stats; reset at the start of the period; manual override by the owner.

### ✅ 12.2 Run log retention and masking
- Configurable per-workflow TTL on runs and node runs (global default `GRAPH_WORKFLOW_RUNS_RETENTION_DAYS`); periodic purge.
- **Redaction**: fields markable as sensitive in the node config (JSON path) → masked (`***`) in persisted outputs, SSE logs and exports, but available in cleartext to downstream nodes during the run. `$secrets` remain never serialized (1.3).

---

## Phase 13 — Copilot and workflow-as-code ✅ COMPLETED (2026-07-21)

Goal: an editor that helps write and repair, and definitions treated as source. Reuses `/generate` (5.3), versioning (1.4), export/import (5.2).

> **Status:** implemented and tested as **Phase 45** (8 new backend tests in `test_phase45.py`; 13.1 is frontend-only and has no backend surface).
> 13.1 — a framework-free `getSuggestions(text, cursor, ctx)` (`frontend/src/app/features/workflows/editor/expression-autocomplete.ts`) wired into the node inspector's `expression`-kind fields and the expression tester: typing `$node.` proposes upstream node ids (computed by a backward BFS over the current canvas edges — no server round-trip), then that node's known output field names (from the fase 3.2 pinned output or the last run's live output); `$vars.`/`$secrets.` complete against the workflow's declared variables and the profile's secret *names* (never values); a bare `$` also offers `$item`/`$index` when the field belongs to a node reachable from a for/repeat's `loop` handle.
> 13.2 — `POST /v1/graph-workflows/runs/{run_id}/explain`: the first failed node of the run (`repo.first_error_node`), its catalog entry, current params, input and error go to the LLM via the same `_llm_json_call` used by 5.3's generator, asking for `{explanation, proposed_params}`. `proposed_params` is optional — the model returns `null` rather than guess when unsure — and is never applied automatically; the backend only computes a display-only add/remove/replace diff (`_shallow_json_diff`) against the node's current params. The run panel shows an "Explain / repair" button on any failed node, renders the explanation and diff, and an **Accept** button merges `proposed_params` into the node's params in the editor (still requires a normal Save) while **Discard** drops it.
> 13.3 — `git_repo_url`/`git_branch`/`git_token_secret`/`git_subpath`/`git_last_synced_at` columns on `workflows` (`PUT /{id}/git-sync` to configure, empty `repo_url` disables). Every subsequent saved version (`POST`/`PATCH` bumping the version) shells out to `git` (subprocess — no Python git dependency) to clone-or-fetch a per-workflow local working copy, write the fase-5.2 export envelope to `<subpath|workflows/<id>.json>`, commit `"<name> v<version> (by <email>)"` and push; failures are logged and swallowed (`git_sync_push_version` must never break the save it's reacting to). `POST /{id}/git-sync/pull` fetches the branch and, when the file's graph differs from the latest known version, adds it as a new **draft** `workflow_versions` row (`repo.add_draft_version`) — the live graph is never overwritten; the pulled draft is reviewed/restored/diffed like any other version (8.1, 1.4). An access token is injected into the HTTPS remote URL only for the duration of each git subprocess call, from a `$secrets` entry named by `git_token_secret` (its value is never accepted or returned by the API). Requires `git` in the backend image (added to `backend/Dockerfile`).
> Docs updated in 5 languages + developer-guide + `.env.example`; editor UI: **Git sync** subsection next to versions in the run panel, **Explain / repair** action + diff under each failed node.

### ✅ 13.1 Expression autocomplete
`expression-input` with contextual suggestions: typing `$node.` proposes the ids of upstream nodes; after the id, the real fields of the output (from the pin schema 3.2 or the last run). Same for `$vars`, `$secrets` (names only), `$item`/`$index` in loop bodies. The single feature that most reduces expression mistakes.

### ✅ 13.2 "Explain / repair" with LLM
On a failed run: a button that passes the graph, error, failed node's input and catalog to the LLM → a two-part answer: explanation of the cause and a **proposed config diff** (JSON patch on the node), to accept or discard in the editor. Reuses the provider layer and the validation/normalization mechanics of 5.3; never auto-applied.

### ✅ 13.3 Git sync of definitions
Automatic export of every version (1.4) as a JSON file to a configured Git repo (`$secrets` for the token, per-workspace path): one commit per version, message with author and version name. Import on pull (manual or webhook): changed definitions become new draft versions. Opens up PR review of workflows ("workflow-as-code").

---

## Phase 14 — Remote execution and scalability ✅ COMPLETED (2026-07-21)

Goal: escape the single process — execute nodes where they are needed (private networks, GPU machines, isolation) and withstand load across multiple instances. Architecturally the most demanding phase: it goes last because all the previous ones reduce its cost (contracts 6.4, checkpoints 2.4, rate limit 6.6, audit 7.3).

> **Status:** implemented and tested as **Phase 46** (25 new backend tests in `test_phase46.py`).
> 14.1 — `workflow_runners`/`workflow_runner_jobs` tables; `POST /graph-workflows/runners` issues a one-time raw token (only its sha256 is stored), `GET`/`DELETE` list/revoke; the runner process (`X-Runner-Token`, no user session) calls `POST /wf/runners/heartbeat`, long-polls `GET /wf/runners/jobs/next` and posts back `POST /wf/runners/jobs/{id}/result` — the exact `test_node()` (3.1) `{ok, output, handles, logs}` contract. A new `runOn` (label) + `runOnFallback` (`fail`|`local`) field pair on `GraphNode` routes a node to the first online runner carrying that label and (empty, or) allow-listing the node's type; `_dispatch_remote` creates the job, polls `workflow_runner_jobs` up to the node's own `timeoutMs` (or `GRAPH_WORKFLOW_RUNNER_JOB_TIMEOUT`), then raises (subject to the node's ordinary retry/onError) or falls back locally. Only a **stateless-safe subset** of node types can be routed remotely — `http.request`, `code`, `db.query`, `set`, `if`, `switch`, `merge`, `filter`, `aggregate`, `batch`, `wait`, `queue.publish` (`_REMOTE_CAPABLE_TYPES`) — because those need no `db`/profile/vault context; `$secrets` referenced in the node's params are already resolved to literal values by the time the job is built, so the runner never sees the vault. `tool.*`/`llm.*`/`subworkflow`/human-in-the-loop nodes silently ignore `runOn` and always run on the backend — the scoped-down piece of 14.1 vs. the ideal (no full remote *run* orchestration, only single stateless nodes, matching the roadmap's own "the runner executes single nodes, not whole runs"). A minimal **Runners** page (`/graph-workflows/runners`) lists online/offline, labels, allowed types and version, registers a new runner (token shown once) and revokes existing ones. Connection is HTTP long-poll (not WebSocket) — simpler, no new dependency, and outbound-only either way. TLS is deployment-level (terminate HTTPS in front of the backend), not implemented in-app.
> 14.2 — no new work needed: the `code` node already dispatches through the Phase 18 `python_exec` sandbox (`app/tools/code_interpreter.py`) — an isolated subprocess with CPU/memory/wall-clock limits (`CODE_INTERPRETER_*`) and no network access (socket calls stubbed before the user code runs) — both when it executes on the backend and, unchanged, when a remote runner claims a `code` job via `_dispatch_stateless` (same function, same limits). Pinned by `test_code_node_runs_through_the_sandboxed_subprocess`.
> 14.3 — a generic per-run lease: `lease_owner`/`lease_expires_at` columns on `workflow_runs`; `repo.acquire_lease` is a single conditional `UPDATE` that succeeds when the run is unleased, already owned by the caller (renewal) or the existing lease has expired. `_execute` claims the lease before doing any work and bails out immediately (no double-execution) if another live instance holds it, renews it on every checkpoint (each node-wave), and releases it when the run ends. On SQLite (single writer, single process today) this is inert bookkeeping that always succeeds; the mechanism itself needs no Postgres migration and is ready for a multi-replica deployment once the storage layer is (the roadmap's own caveat — "requires Postgres... SQLite remains for single-node" — still applies to *actual* concurrent replicas, just not to the lease code).
> 14.4 — a pluggable `QueueDriver` ABC (`publish`/`consume`) with two shipped drivers, no new dependency (neither `pika`/AMQP nor `kafka-python`/`paho-mqtt` were available to install in this environment, nor genuinely testable without a live broker): `db` (default, `GRAPH_WORKFLOW_QUEUE_DRIVER=db`) persists messages in a new `workflow_queue_messages` table, surviving restarts; `memory` is a per-process `asyncio`-friendly dict, zero setup, for tests/dev. `queue.publish` (new node) calls `driver.publish(topic, message, headers)`; the `queue.consume` trigger reuses the existing file.watch/email.inbound poll loop (`list_due_poll_triggers`, `_poll_queue_consume`) to drain pending messages and fire one run per message with `$trigger = {message, topic, headers}`. A real broker (RabbitMQ/Kafka/MQTT) plugs in as a third `QueueDriver` subclass — the node/trigger/poll-loop code never changes, only `get_queue_driver()`'s selection.
> 14.5 — `sibyl-wf` (`python -m app.cli.sibyl_wf`): `run <id> [--trigger file.json]`, `export <id> [--out file]`, `import <file>`, `test <id> <node_id> [--input file.json]`, `logs <run_id>`. Built on `httpx.AsyncClient` against the existing REST API — no direct DB access. "API-key auth" is a bearer access token (`SIBYL_API_KEY`/`--api-key`, e.g. minted by `POST /auth/login`) sent as `Authorization: Bearer <key>`, since the product has no separate API-key subsystem outside JWT sessions; `SIBYL_PROFILE_ID` sets `X-Profile-ID` when needed.
> New settings (`.env.example`): `GRAPH_WORKFLOW_RUNNER_HEARTBEAT_TIMEOUT`, `GRAPH_WORKFLOW_RUNNER_JOB_TIMEOUT`, `GRAPH_WORKFLOW_RUNNER_POLL_INTERVAL`, `GRAPH_WORKFLOW_LEASE_TTL_SECONDS`, `GRAPH_WORKFLOW_QUEUE_DRIVER`, `GRAPH_WORKFLOW_QUEUE_POLL_SECONDS`.

### ✅ 14.1 Remote runner (agent on another server)
A **runner** is a lightweight process (same repo, `pip install`/dedicated container) installed on another server, which registers with the backend using a token and receives work. Architecture:

- **Outbound-only connections** from the runner (long-poll or WebSocket towards the backend): works behind NAT/firewalls without opening ports, like GitHub Actions runners.
- **Registration and labels**: the runner introduces itself with a name + labels (`gpu`, `internal-network`, `dmz`); periodic heartbeat; state visible in a **Runners** page (online/offline, version, runs in progress).
- **Routing**: `runOn` field (label) on the node or on the whole workflow; without a label, execution stays on the backend as today. The dispatcher assigns the node run to the first compatible free runner; if no runner is online, the node waits with a timeout (`onTimeout` → fail or local fallback, configurable).
- **Execution contract**: the backend sends `{node, resolved config, input}` and receives `{ok, output, handles, logs}` — the same contract as `test_node()` (3.1), which becomes the protocol. The graph and orchestration stay on the backend: the runner executes *single nodes*, not whole runs (checkpoint 2.4 remains central and resume keeps working).
- **Security**: revocable per-runner tokens; referenced `$secrets` are delivered already resolved and only for the assigned node, never the whole vault; TLS encryption; allow-list of executable node types per runner (e.g. a DMZ runner executes only `http.request`).
- **Use cases**: calling APIs reachable only from the customer's internal network, `db.query` on non-exposed databases, heavy `code`/tool nodes on big machines, local inference on GPU hosts.

### ✅ 14.2 `code` node sandbox
Today the `code` node runs in the backend process. Sandboxed execution: subprocess with limits (CPU/memory/time, no network by default) or throwaway container; on remote runners (14.1) the sandbox is the default. Prerequisite for letting untrusted users of shared workspaces (5.2, 7.3) write `code` nodes.

### ✅ 14.3 Engine scale-out
Multiple backend replicas sharing the load: per-run lease/lock in the DB (a run belongs to one instance; heartbeat, takeover by another replica via checkpoint 2.4 if the lease expires), scheduler and queues (2.3) coordinated via the DB. Requires Postgres as storage (SQLite remains for single-node). To be done only when real load justifies it.

### ✅ 14.4 Message queue triggers
Consumer triggers for external brokers: `queue.consume` (AMQP/RabbitMQ, Kafka, MQTT — one connector at a time, in that order of demand), credentials from `$secrets`, `$trigger = {message, topic, headers}`, ack on run completion (at-least-once). The natural outbound complement is a `queue.publish` node. With 14.1, the consumer can run on a runner inside the broker's network.

### ✅ 14.5 CLI
`sibyl-wf` (or a backend subcommand): `run <id> [--trigger file.json]`, `export/import`, `test` (11.1), `logs <run_id>` — against the existing APIs, API key authentication. Serves CI (workflow tests in pipelines with 13.3) and UI-less operations.

---

## Phase 15 — Connectors and multimodal nodes ✅ COMPLETED (2026-07-22)

Goal: widen what a workflow can touch — curated integrations on top of `http.request` and media beyond text/JSON. Independent items; each one lands as ordinary catalog nodes (4.3), so retry (2.1), node test (3.1) and pins (3.2) apply for free.

> **Status:** shipped as Phase 47 (18 tests in `tests/test_phase47.py`, green).
> 15.1 — seven `connector.<service>.<op>` nodes in a new **Connectors** palette category, each a one-line entry in the `_CONNECTORS` registry (a pure mapper → an `http.request` spec, so retry/rate-limit/test/pin come free).
> 15.2 — `ssh.exec` over paramiko with the `GRAPH_WORKFLOW_SSH_ALLOWED_HOSTS` allow-list and `allow_nonzero`.
> 15.3 — `browser` over Playwright (text/attribute/screenshot), thread-isolated with a per-action timeout; clear error when Playwright is absent.
> 15.4 — `rss.read` trigger reusing the poll loop, dependency-free RSS/Atom parse, guid dedup, first-poll seeding.
> 15.5 — `doc.convert` via markitdown; the media nodes (`audio.transcribe`/`image.ocr`/`image.generate`/`tts`) are deferred pending provider-layer support.

### ✅ 15.1 Curated connector library
Prebuilt nodes for the top requested services — Slack/Discord (post message), GitHub/GitLab (create issue), Jira (create issue), Google Sheets (read/append) — implemented over `http.request` but with auth (`$secrets` mapping), operations and payloads already wired. OpenAPI import (9.4) is the generic path; these are the hand-tuned first cut with a dedicated palette category. (S3/FTP get/put deferred — they need a non-HTTP client rather than the `http.request` mapper.)

### ✅ 15.2 `ssh.exec` node
Runs a command on a remote server over SSH (key/password from `$secrets`, host allow-list per instance), output `{stdout, stderr, exit_code}`, configurable timeout, non-zero exit raises (so retry / On error apply) unless `allow_nonzero`. The lightweight sibling of the remote runner (14.1): covers "run that script on that machine" without installing anything remotely.

### ✅ 15.3 `browser` node (Playwright)
Open page, wait for selector, extract content (text/attribute), screenshot — scraping and checks on sites without an API. Execution happens in a worker thread with a per-action timeout; requires `playwright` (+ a browser) in the image, otherwise the node raises a clear error.

### ✅ 15.4 `rss.read` trigger
RSS/Atom feed polling with per-guid dedup (reuses the poll loop); `$trigger = {title, link, published, summary, guid}`, one run per new entry. Highly requested for "news → LLM → notify" flows.

### ✅ 15.5 Multimodal nodes
- `doc.convert`: PDF/DOCX/HTML/… → markdown via markitdown (already in the backend image for the KB), output `{markdown, chars}`. ✅
- `audio.transcribe`: audio file from workspace storage → text (provider layer / Whisper API), output `{text, segments}`. ⬜ deferred (needs provider-layer transcription).
- `image.ocr`: image → text. ⬜ deferred.
- `image.generate` and `tts` where the provider layer supports them, with outputs written to workspace storage (readable by `file.*` 4.2). ⬜ deferred.
All file I/O confined to `GRAPH_WORKFLOW_FILES_DIR` like the 4.2 nodes.

---

## Phase 16 — State and execution semantics ✅ COMPLETED (2026-07-22)

Goal: patterns real integrations need — memory across runs, exactly-once behavior, rollback. Small engine features with outsized practical value; 16.1 and 16.2 also serve most Phase 6 triggers. Shipped as **Phase 48**.

### ✅ 16.1 Persistent state across runs
`state.get` / `state.set` / `state.increment` nodes over a per-workflow key-value store (`workflow_state` table, JSON values, optional TTL per key): counters, pagination cursors, "last processed ID". Today this is simulated with `file.*` or `db.query`. State visible/editable in the run panel via `GET/PUT/DELETE /{id}/state` (with the audit 7.3 recording manual edits); excluded from export by default (it lives in a separate table). `state.increment` is atomic on SQLite's single writer.

### ✅ 16.2 Trigger idempotency
Dedup key on webhook/event triggers (a `dedupKey` expression over the payload, e.g. `{{ $trigger.order_id }}`): the same notification delivered twice does not produce two runs (second delivery returns the existing `run_id`, HTTP 200, `deduped: true`). Configurable window (`dedupWindowSeconds`, default from `GRAPH_WORKFLOW_DEDUP_DEFAULT_WINDOW_SECONDS`); keys stored with TTL in `workflow_trigger_dedup`. Essential with external systems that retry deliveries.

### ✅ 16.3 Compensations (saga)
A `compensate` handle on nodes with side effects: when the run fails downstream, the engine walks the completed nodes in reverse order and executes the subgraph hanging off each node's `compensate` edge (e.g. release the reserved stock if payment fails), with each compensation receiving the original node's output. Compensation node runs are tagged on the live stream (`compensation: true`); a failure inside a compensation marks the run `failed` with a compound error. Opt-in per node (wire a `compensate` edge) — no behavior change for existing graphs.

### ✅ 16.4 Run priority
`priority` field on runs (from trigger config `priority` or the launch API `priority`): the per-workflow queue (2.3) and, later, the scale-out dispatcher (14.3) serve higher priority first, FIFO within the same priority. Lets interactive runs jump ahead of batch backfills.

---

## Phase 17 — Scheduling, SLA and scale UX ✅ COMPLETED (2026-07-22)

Goal: operating dozens of workflows without babysitting them. Complements per-node metrics (7.4) and budgets (12.1).

> **Status:** implemented and tested as **Phase 49, v3.5.0** (11 new backend tests in `test_phase49.py`). Backend-complete; the editor/navigator UI reuses the existing settings PATCH surface and can layer on later.

### ✅ 17.1 Calendars and windows
Per-schedule timezone (`tz` on the schedule trigger config — one workflow can carry schedules in several zones); holiday **skip dates** (`skip_dates: ["YYYY-MM-DD"]` on the schedule or the workflow, evaluated in the schedule's own timezone) and **blackout windows** (`blackout.windows: [{start:"HH:MM", end:"HH:MM", days:[0-6]?}]`, an `end <= start` wrapping past midnight) at workflow level. A schedule due inside a window / on a skip date is not run: `blackout.on_conflict` chooses `"skip"` (advance to the next recurrence) or `"defer"` (retry every `_BLACKOUT_DEFER_SECONDS` until the window clears). All decided in `_schedule_blocked` inside the poll loop, before the run is created.

### ✅ 17.2 SLA monitors
Per-workflow `sla` config: `{max_duration_s, missed_grace_s, channels:[inapp,telegram]}`. A scheduler sweep (`check_sla_monitors`) raises a **one-time** alert when a run overruns `max_duration_s` (running or terminal, elapsed from `created_at`; deduped by a `sla_alerted` flag on the run) or when an enabled schedule is overdue past `missed_grace_s` — the run never started (deduped by `last_sla_alert_at` on the trigger). Complementary to the `error` trigger, which only sees explicit failures. Alerts route to the same notify channels as the digest.

### ✅ 17.3 Folders, tags and search
`folder`, `tags` and `archived` on workflows; `GET /search` does full-text over name, description **and node contents** (so `q=slack` finds a workflow that merely uses a Slack node), filtered by `folder`/`tag`, archived hidden unless `include_archived=true`; `GET /folders` returns the folder tree. The collapsible list from Phase 36 becomes a real navigator.

### ✅ 17.4 Run comparison
`GET /runs/compare?a=&b=` diffs two runs of the same workflow: per-node `status`/`duration_ms`/`output` for each side, `output_equal` per node, and `first_divergent_node` (first node in run A's execution order that differs) — the answer to "why did it work yesterday?". Payloads are carried only for the nodes that differ, keeping the diff light. Complementary to the version diff (8.1), which compares definitions rather than executions.

### ✅ 17.5 Notification digest
Per-workflow `notify.digest: {enabled, interval_s, channel}`. When enabled, each terminal run buffers one row in `workflow_notification_digest` instead of an immediate message; a scheduler sweep (`flush_notification_digests`) delivers one summary per `(workflow, channel)` bucket — counts by outcome — once its oldest entry is older than `interval_s`, then clears it. Opt-in, so workflows that didn't ask for it are never touched; `error`/`waiting` alerting (error triggers, recurring-failure alerts) stays immediate.

---

## Phase 18 — LLM quality

Goal: close the loop on LLM-heavy workflows — measure and gate output quality instead of hoping. Reuses the provider layer, per-node metrics (7.4) and stats (5.1).

> **Status:** shipped as Phase 50 (11 tests in `tests/test_phase50.py`, green).

### ✅ 18.1 `llm.judge` node
Evaluates another node's output against given criteria (rubric in the config, score range + rationale), output `{score, verdict, passed, rationale}` with a configurable threshold driving `pass`/`fail` handles. Enables "generate → judge → regenerate" loops (with `while` 6.3) and quality gates before a notification/publication. Shares model picker/failover/cache with the other `llm.*` nodes; judge model can differ from the generator's.
> Implemented: 1..`scaleMax` scale (default from `GRAPH_WORKFLOW_JUDGE_DEFAULT_SCALE_MAX`), `threshold` defaulting to 60% of the scale, score clamped to range and `passed` decided by the threshold **authoritatively** (the model's own `verdict` never overrides the gate). Missing `criteria` / non-numeric score raise, so retry / On error apply. Curated example `llm-quality-gate`.

### ✅ 18.2 Prompt A/B testing
Two (or N) prompt/model variants on an `llm.*` node, alternated across runs (round-robin or weighted); each node run records its variant, and the per-node metrics (7.4) plus `llm.judge` scores (18.1) break down by variant to declare a winner. "Promote variant" collapses the node back to the winning configuration (a new version, 1.4).
> Implemented: `variants: [{name, weight?, params:{overrides}}]` + `variantStrategy` (`round-robin` — a per-node counter persisted in `workflow_state`, survives restarts — or `weighted`). Selected once per node run (before retries), the variant's params overlay the node's own and the choice is stamped on the output (`_variant`). `GET /{id}/nodes/{node_id}/variants` aggregates executions / ok-rate / mean judge score / pass-rate / tokens per variant and flags the `winner`. The one-click "promote variant" UI collapse is the remaining editor-side follow-up.

---

## Phase 19 — Custom Node SDK (end-user nodes)

Goal: users extend the palette themselves — from config-only nodes to uploaded code — without forking the product. This is a platform feature: it goes after the sandbox (14.2), which is its security prerequisite for code nodes, and reuses catalog (4.3), import warnings (5.2), sharing (5.2), audit (7.3).

### ⬜ 19.1 Node manifest and packaging
A custom node is a package (zip or single file) with a **manifest** (`node.json`):
`{type, name, description, category, icon, version, params: JSONSchema, outputs: JSONSchema, handles: [...], secrets: [names], permissions: [network|files|db], engineMin}`.
Two implementation tiers, declared in the manifest:
- **Declarative** (`kind: declarative`): no code — the node is a parameterized template over existing nodes (typically `http.request`): URL/method/headers/body with `{{param}}` placeholders, response mapping to the declared output schema. Safe by construction; this is the n8n-style "declarative node" and should cover most community connectors.
- **Code** (`kind: python`): a Python module exposing `async def run(params, input, ctx) -> dict`; `ctx` offers only the declared capabilities (`ctx.http`, `ctx.files`, `ctx.secrets[name]` limited to manifest-declared names — never the vault, `ctx.log`). Output validated against the manifest's output schema.

### ⬜ 19.2 Upload, registry and lifecycle
- **Custom Nodes** page: upload package → validation (manifest schema, type-name collision with builtin/other customs, code lint/import check) → the node appears in the palette with a "custom" badge and its icon.
- Versioned like workflows: uploading again creates a new node version; graphs record the node version they were built with; older versions keep running until migrated (banner in the inspector when a newer version exists).
- Enable/disable per node; delete blocked while any workflow references it (list of dependents shown).
- API: `GET/POST /custom-nodes`, `GET /custom-nodes/{type}`, `POST /custom-nodes/{type}/versions`, `DELETE`; storage under `GRAPH_WORKFLOW_CUSTOM_NODES_DIR` + DB registry table.

### ⬜ 19.3 Security model
- Declarative nodes: same trust level as the graphs themselves — installable by any `editor`.
- Code nodes: **always** executed in the sandbox (14.2) with the manifest's declared permissions as the ceiling (no network unless `network`, file access confined to workspace storage, CPU/memory/time caps); installable only by the profile owner (and, in shared workspaces, only by admins — roles 7.3); install/update/enable events audited (7.3).
- Secrets: the node receives only the secrets it declared and the user explicitly bound at install time (a consent screen lists them); values delivered per-execution, never stored with the package.
- Optional signature: a workspace can require packages signed with a known key before install (`GRAPH_WORKFLOW_REQUIRE_SIGNED_NODES`).

### ⬜ 19.4 Developer experience
- `sibyl-wf node init` (CLI 14.5): scaffolds manifest + module + a fixture test; `sibyl-wf node test` runs it locally against the `test_node()` contract (3.1); `sibyl-wf node pack/push` uploads.
- Hot reload in dev: re-upload replaces the dev version without bumping; the single-node test (3.1) works on custom nodes exactly like builtins.
- Docs: authoring guide + annotated example (one declarative connector, one Python node).

### ⬜ 19.5 Distribution
- Export/import (5.2): a workflow's export lists its custom node dependencies `{type, version}`; import warns when they're missing (same toast mechanics as missing `$secrets`) and offers one-click install when the package is available in the workspace.
- Workspace sharing (5.2 pattern): publish a custom node to a workspace; members install it into their profile. A public community marketplace is a possible later step on the same registry — out of scope here.

---

## Phase 20 — Telegram as a first-class workflow channel

Goal: promote Telegram from a notification sink and approval surface to a **bidirectional workflow channel** — inbound messages/commands/media that *start* workflows, and outbound nodes that send, update and interact from any point in a graph. Everything here builds on what already exists: the live bot instance (`app.telegram.bot.get_bot()`), the linked-profile model (`telegram_link_repository`, `/link` codes), `notification_service.notify_telegram` (opt-in gate `is_notify_enabled`, inline-keyboard support, 4096-char chunking), the fase 7.5 approval callback path and the fase 9.3 `chat` trigger / `chat.reply` round-trip. The aim is to generalise those one-off bridges into reusable trigger + node primitives, so retry (2.1), node test (3.1), pins (3.2) and idempotency (16.2) apply for free.

### ⬜ 20.1 `telegram` trigger
A dedicated inbound trigger, distinct from the generic `chat` trigger (9.3): bind a workflow to a **bot command** (`/report`), a message pattern, or "any message" scoped to a chat/group/forum-topic allow-list. `$trigger = {chat_id, thread_id, user: {id, username, profile_id?}, text, command, args, message_id, channel_post?}`. The linked web profile (when the sender has run `/link`) is resolved and attached, so the run executes under that profile's identity, secrets and quotas; unlinked senders are gated by `telegram_allowed_users` exactly as the bot is today. Dedup by `(chat_id, message_id)` reuses the fase 16.2 `workflow_trigger_dedup` machinery so retries never double-fire. The bot's existing handlers (`cmd_*`, `handle_document/photo/voice`) route to matching workflow bindings before falling through to the default chat loop.

### ⬜ 20.2 `telegram.send` and message nodes
Outbound action nodes that talk to any chat, not only the origin or the linked profile:
- `telegram.send` — text to an explicit `chat_id`/`thread_id` (expression), `parse_mode`, `disable_preview`, optional `reply_to`; output `{message_id, chat_id}` so later nodes can edit it.
- `telegram.sendMedia` — photo/document/audio/voice/video from `GRAPH_WORKFLOW_FILES_DIR` (4.2) or a URL, with caption.
- `telegram.editMessage` / `telegram.deleteMessage` — update or remove a message sent earlier in the run (live progress bars, "processing…→done" edits).
- `telegram.sendPoll` / `telegram.sendLocation` for the common rich types.
All reuse the `notify_telegram` send/chunk/rate-limit path (never blocking the scheduler on Telegram's limits) and no-op cleanly when the bot is not running, mirroring today's silent-drop semantics. Sending to a chat the instance doesn't own raises a typed error so `On error` (2.x) applies.

### ⬜ 20.3 Interactive inline keyboards (generic)
Generalise the fase 7.5 approval buttons into a first-class interaction primitive so any node can ask a Telegram question:
- `human.approval` / `human.input` (10.1) render their choices/form as an inline keyboard on Telegram when the run originates from — or is bound to — a chat; the callback resumes the `waiting` run through the existing approve/submit path, no new state machine.
- A standalone `telegram.ask` node: present buttons, suspend the run (reusing `wait.event` correlation, 10.2), resume with the chosen `callback_data` as output; configurable timeout + `onTimeout` branch. Callback queries are answered (`answerCallbackQuery`) to clear the client spinner, and the prompt message is optionally edited to show the decision — closing the loop the current approval flow leaves half-open.

### ⬜ 20.4 Inbound media and file ingestion
When a `telegram` trigger (20.1) fires on a document/photo/voice/video, the file is fetched via the Bot API and written to `GRAPH_WORKFLOW_FILES_DIR`, exposed on `$trigger` as `{file: {path, mime, name, size}}` — directly consumable by `file.*` (4.2), `doc.convert` (15.5), `kb.search` (6.x) or a future `audio.transcribe`. This reuses the bot's existing `handle_document`/`handle_photo`/`handle_voice` download logic, lifted into a shared helper so both the chat loop and workflow triggers share one code path. Size/MIME limits per instance (`GRAPH_WORKFLOW_TELEGRAM_MAX_FILE_MB`).

### ⬜ 20.5 Bot binding and multi-bot (optional)
Today a single bot instance serves the deployment. For workflow-facing bots, allow a workflow (or workspace) to declare a **binding**: which bot commands it owns (registered via `setMyCommands` on boot so they appear in the Telegram UI), and — where operators want isolation — an optional dedicated bot token from `$secrets`, run as an additional polling application alongside the main bot. Command↔workflow bindings live in a small registry table; collisions (two workflows claiming `/report`) are rejected at save time. Kept last and optional: the single-bot path (20.1–20.4) covers the majority; a dedicated token is the escape hatch for teams that want a branded, separate bot without standing up another deployment.

---

## Order and rationale summary

| Phase | Theme | Key items | Unlocks | Status |
|---|---|---|---|---|
| 1 | Foundations | Editor refactor, navigation shell, vars/secrets, versioning | Everything else at lower cost | ✅ Done |
| 2 | Reliability | Retry, parallelism, queue, checkpoints, error trigger | Real production; human.approval | ✅ Done |
| 3 | Editor DX | Node test, data pins, run overlay, undo/redo, templates | 10× faster workflow development | ✅ Done |
| 4 | New nodes | classify/extract, db/file, tool registry, human.approval | New use cases | ✅ Done |
| 5 | Product | Metrics/costs, export/import, LLM generation | Commercial value | ✅ Done |
| 6 | Engine extension | Success/file/email triggers, `while`, subworkflow contracts, `kb.search`, rate limit | New integration patterns | ✅ Done |
| 7 | Operations | Replay from failed node, dev/prod environments, audit+roles, per-node metrics, Telegram approval | Production-grade management | ✅ Done |
| 8 | Advanced editor | Visual version diff, notes/frames, step-by-step debugging | Large graphs and debugging | ⬜ To do |
| 9 | Ecosystem | Workflow as a tool (chat/agent/MCP), chat trigger, OpenAPI import | The product multiplier | ✅ Done |
| 10 | Advanced HITL | `human.input` (form), `wait.event` with correlation | Real async processes | ⬜ To do |
| 11 | Quality | Test suites, dry-run, cost estimate | Workflows treated like code | ⬜ To do |
| 12 | Governance | Token budgets/quotas, retention + redaction | Production without surprises | ✅ Done |
| 13 | Copilot & as-code | Expression autocomplete, LLM explain/repair, Git sync | DX and PR review | ⬜ To do |
| 14 | Scale & remote | Remote runner, `code` sandbox, scale-out, MQ triggers, CLI | Private networks, GPU, multi-instance | ⬜ To do |
| 15 | Connectors & multimodal | Curated connectors, `ssh.exec`, `browser`, `rss.read`, transcribe/OCR/doc.convert | Reach beyond text/JSON APIs | ⬜ To do |
| 16 | Execution semantics | Persistent state, trigger idempotency, saga compensations, run priority | Real-world integration patterns | ⬜ To do |
| 17 | Scheduling & scale UX | Calendars/blackouts, SLA monitors, folders/tags/search, run comparison, digests | Dozens of workflows without babysitting | ⬜ To do |
| 18 | LLM quality | `llm.judge`, prompt A/B testing | Measured, gated LLM output | ✅ Done |
| 19 | Custom Node SDK | Manifest + declarative/Python nodes, registry, sandboxed security model, CLI DX, distribution | User-extensible palette, community connectors | ⬜ To do |
| 20 | Telegram channel | `telegram` trigger, `telegram.send`/edit/media nodes, generic inline keyboards, inbound file ingestion, bot binding | Telegram as a bidirectional workflow channel | ⬜ To do |

Recommended first sprint (phases 1–5): **1.1 + 1.2** (UI refactoring) in parallel with **2.1** (per-node retry, backend only) — no cross-dependencies and immediate value on both fronts.

Recommended next sprint (phases 7–8): **7.1** (replay/retry from here) + **7.5** (Telegram approval) + **8.1** (visual version diff) — all reuse existing infrastructure (checkpoints, approvals API, versioning + graph-preview), no refactoring, immediate user value.

Note on the ordering of phases 9–14: 9 (ecosystem) is the product priority and depends only on 6.4 (✅ done); 10–13 are mutually independent and can be tackled in any order; 14 goes last — it is the only one touching the execution architecture, and it is best faced once contracts (6.4), node-test as protocol (3.1) and governance (7.3, 12) are consolidated.

Note on phases 15–19: 15–18 are mutually independent grab-bags — individual items can be cherry-picked into any sprint (16.1 idempotency and 16.2 state are natural companions to the Phase 6 triggers; 15.2 `ssh.exec` is a cheap stand-in while 14.1 is pending). 19 (Custom Node SDK) is the exception: its declarative tier (19.1) can ship early, but the code tier hard-depends on the sandbox (14.2) and the roles/audit of 7.3 — do not ship user-uploaded code before those exist.

Note on phase 20 (Telegram channel): it depends only on already-shipped infrastructure — the live bot, `notify_telegram`, the linked-profile model, the fase 7.5 approval callback, the fase 9.3 `chat` trigger and fase 16.2 trigger idempotency — so it carries no ordering constraint and can be picked up at any time. Suggested sub-order: **20.1** (inbound trigger) + **20.2** (outbound send) first, since together they already enable request→process→reply bots; **20.3** (interactive keyboards) next as it upgrades the existing approval UX; **20.4/20.5** (media ingestion, dedicated bots) are independent add-ons. Only 20.5's dedicated-token path touches process/boot wiring — keep it last and optional.
