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

> **In a hurry?** Click ✨ on the `/graph-workflows` page and **Import** one of the four
> ready-made [example graphs](../examples/graph-workflows.md) — it opens on the canvas
> ready to edit and run.

## The canvas

The editor is a three-pane layout:

- **Left** — your workflows and a categorised **node palette** (Triggers · Actions ·
  Logic · Data · AI). Every built-in, MCP and custom tool automatically appears as a
  `tool.<name>` action node — no new code per tool.
- **Center** — a dependency-free **SVG canvas**. Drag nodes to lay them out; drag from a
  node's **output handle** (right) to another node's **input handle** (left) to connect
  them. Click an edge to delete it.
- **Right** — the **inspector** for the selected node (its parameters, rendered from the
  node type's schema), or, when nothing is selected, the **run & triggers panel**.

Save with **Save**, flip **Active** to let triggers fire, and **Run now** to execute the
graph immediately — nodes light up green/blue/red/grey (ok/running/error/skipped) live as
the engine streams progress over SSE.

## Node types

| Category | Nodes |
|----------|-------|
| **Trigger** | `manual`, `schedule`, `webhook`, `event` |
| **Action** | `tool.<name>` — any **built-in** tool (RSS, read_url, weather, kb_search, http_request, python_exec…) |
| **MCP & custom** | every **discovered MCP server tool** (`tool.mcp__<server>__<tool>`) and the profile's **custom HTTP tools** (`tool.custom__<name>`) appear as drag-in nodes — no code per tool |
| **Logic** | `if` (true/false branch), `switch` (case branches), `merge` (collect inputs), `for` (for-each over an array), `repeat` (N times) |
| **Data** | `set` (build an object), `filter` (keep matching array items), `code` (Python sandbox) |
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

Anything not starting with `=` is a plain literal.

## Triggers

Attach triggers from the run panel:

- **Schedule** — cron / RRULE / natural language ("every day at 9:00"), parsed by the
  same engine as reminders. A background poll loop fires due schedules and recomputes the
  next run time. (Only fires while the workflow is **Active**.)
- **Webhook** — a public, token-scoped URL (`POST /api/v1/wf/hooks/{token}`). The JSON
  body becomes `$trigger`. Only fires while the workflow is Active.
- **Event** — internal events (e.g. a document ingested, a reminder fired).

## Versioning & runs

Every save snapshots an immutable version; you can list versions and roll back. Each run
stores the executed graph, the resolved run context, and a per-node record (input, output,
error, timing) you can inspect after the fact.

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
GET    /v1/graph-workflows/{id}/runs
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
