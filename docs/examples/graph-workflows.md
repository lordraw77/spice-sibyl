# Example graph workflows (Phase 29)

A curated set of ready-to-run **visual node-graph** workflows ships with the repo.
They appear in the **Examples** gallery on the `/graph-workflows` page (the ✨ button)
and import with one click: importing **creates a new workflow from the example's
graph**, then opens it in the editor so you can inspect, tweak, and **Run now**.
Since roadmap fase 3.6 the gallery is a proper **template gallery**: each card shows a
**miniature preview of the graph** and the cards can be **filtered by category** before
importing.

Each example only uses node types the engine ships and tools present in
`app/tools/registry.py`. A CI smoke test
(`backend/tests/test_phase29.py::test_examples_catalog_is_valid_and_importable`)
asserts every node type used is still in the palette catalog (`GET /v1/graph-workflows/node-types`),
so a tool rename or a dropped node kind can never silently strand a one-click example.
The examples themselves live in
[`backend/app/examples/graph_workflow_examples.py`](../../backend/app/examples/graph_workflow_examples.py)
and are served read-only from `GET /v1/graph-workflows/examples`.

## How to use

1. Open **/graph-workflows** and click the ✨ button, then **Import** on a card.
2. The new workflow opens on the canvas — inspect and edit nodes as you like.
3. Press **Run now** to execute it and watch nodes colour live, or attach a
   **schedule**/**webhook** trigger and flip the workflow **Active**.

Some examples need a linked prerequisite (a populated knowledge base for `kb_search`,
network access for `fetch_rss`/`read_url`/`get_weather`). Where a prerequisite is
missing the node reports an error rather than fabricating output.

> **Tip — `$vars` and `$secrets` (roadmap fase 1):** any hardcoded value in these
> examples (an RSS URL, a city, an API header) can be lifted into the workflow's
> **Variables** panel and referenced as `{{ $vars.name }}`; credentials belong in the
> **Secrets** panel as `{{ $secrets.NAME }}` — encrypted at rest, never exported, and
> resolved only while a run executes.

> **Tip — copilot (roadmap fase 13):** while editing any example's expression fields,
> typing `$node.`/`$vars.`/`$secrets.` autocompletes against the actual upstream nodes,
> declared variables and secret names of *that* workflow. If a run fails, the failed node
> in the run panel gets an **Explain / repair** button — a plain-language cause plus an
> optional corrected params diff you accept or discard. And any workflow can be pointed at
> a Git repo (run panel → Versions → **Git sync**) so every saved version is committed
> there as JSON, with **Pull now** importing external changes back as a draft version.

> **Tip — remote execution (roadmap fase 14):** any node of a stateless-safe type
> (`http.request`, `code`, `db.query`, `set`, `if`, `switch`, `merge`, `filter`,
> `aggregate`, `batch`, `wait`, `queue.publish`) can carry a **runOn** label in its
> Advanced settings, executing on the first online, allow-listed remote runner instead
> of the backend — register one from **Graph workflows → Runners**, start the agent with
> its issued token (`SIBYL_RUNNER_TOKEN=... python -m app.runner.agent`), and the node's
> `$secrets` reach it already resolved, never the vault. A **Queue publish** node plus a
> **Queue consume** trigger (`$trigger = {message, topic, headers}`) let two workflows
> hand off work through a topic without a real broker (`GRAPH_WORKFLOW_QUEUE_DRIVER=db`
> by default — a real AMQP/Kafka/MQTT adapter is a drop-in `QueueDriver`, not required to
> use the feature). No curated example ships for these two — they need a second process
> (a runner agent, or a producer/consumer pair) to be meaningful, unlike the single-click
> imports above; see the developer guide's Phase 46 section for the request/response shapes.

---

## 1. RSS morning digest — `rss-morning-digest`

`schedule → tool.fetch_rss → llm.completion → set`

On a schedule, pulls the latest entries from an RSS/Atom feed
(`https://hnrss.org/frontpage`), has an LLM condense them into five bullets, then
builds a titled digest object. Shows a trigger → action → AI → data-mapping chain and
an expression that pipes one node's output into the next
(`={{ $node.rss.output.result }}`).

## 1a. RSS → LLM → Slack — `rss-to-slack-alert` (Phase 47)

`rss.read → llm.completion → connector.slack.postMessage`

Attach an `rss.read` **trigger** (poll a feed, one run per new entry, deduped by guid);
`$trigger` carries `{title, link, published, summary, guid}`. An LLM writes a one-line
take, then the curated **Slack** connector posts it — no hand-built HTTP call, the bot
token pulled from `$secrets` (`={{ $secrets.SLACK_TOKEN }}`). Shows the fase-15 connector
library and the `rss.read` trigger in one "news → LLM → notify" flow.

## 2. Weather-aware greeting — `weather-greeting`

`manual → tool.get_weather → set`

Run on demand: fetches the current weather for a city and assembles a friendly
greeting that embeds it. The smallest useful flow — a tool node feeding a `set` node
that interpolates the result into a message string.

## 3. Webhook → knowledge-base answer — `webhook-kb-answer`

`webhook → tool.kb_search → llm.completion → set`

Exposes a public webhook whose JSON body (`{ "question": "…" }`) becomes `$trigger`.
Searches your knowledge base for the question, then answers **strictly from the
retrieved passages**. Demonstrates the webhook trigger and reading the trigger payload
with `={{ $trigger.question }}`. Activate the workflow, then `POST` to the trigger's
public URL (`/api/v1/wf/hooks/{token}`).

## 4. Page keyword watcher (branching) — `page-keyword-watch`

`schedule → tool.read_url → if → set | set`

On a schedule, fetches a web page and **branches** on whether a keyword appears: the
`true` branch records an alert, the `false` branch records "no change". Demonstrates
`if`-routing (with the unused branch's node recorded as `skipped`) and a whitelisted
expression using the `in` operator and `lower()`:
`={{ 'sale' in lower($node.fetch.output.result) }}`.

## 5. API call with error fallback — `api-error-fallback`

`manual → http.request (retry ×2, on error → branch) → set | set`

Calls an external HTTP API with the dedicated `http.request` node: two retries with a
one-second backoff, and **On error → Route to error branch**. On success the response is
shaped from `={{ $node.api.output.status }}` / `.text`; when every attempt fails, the
failure flows through the node's **`error` output handle** into an alert node
(`={{ $node.api.output.error }}`) instead of failing the run — a try/catch drawn on the
canvas.

## 6. Compose workflows — `subworkflow-composer`

`manual → subworkflow → set`

Runs **another workflow as a child step** and post-processes its result. Select the
child in the subworkflow node's inspector: the **Workflow** parameter is a dropdown of
your workflows (it also accepts an id or an expression via the API). An optional run
payload (`{ "input": … }` in the run panel's **Run payload** box) is forwarded to the
child as `$trigger`. The
`payload` parameter becomes the child's `$trigger`, and the child's sink output comes
back as `={{ $node.child.output.output }}` (plus `run_id` and `status` for auditing).
The child executes as its own observable run with `trigger_type: subworkflow`.

## 7. Switch routing (multi-branch) — `switch-routing`

`manual → switch → set | set | set`

Routes the run to one of three branches: `switch` resolves its **value** expression
(`={{ default($trigger.channel, 'a') }}`), matches it against the `cases` list and
falls back to the `default` handle. Run it with a payload like `{"channel": "b"}` to
steer the branch; the unpicked branches are recorded as `skipped`.

## 8. Parallel fan-out + merge — `fanout-merge`

`manual → set + set (parallel) → merge → aggregate`

Fans the trigger out to two parallel branches, collects both results with a `merge`
node (output `{items}`), then reduces the list with `aggregate` (`op: sum` over the
`value` field). The minimal scatter/gather pattern.

## 9. Data pipeline: filter + aggregate — `orders-filter-total`

`manual → set → filter → aggregate → set`

Builds a list of orders with a `set` list-literal expression, keeps only the large
ones with `filter` — the boolean keep-mask uses the **`=py:` escape hatch** (a list
comprehension runs in the sandbox: `=py:[o['total'] > 100 for o in …]`) — and sums the
surviving totals with `aggregate`.

## 10. Batch + for-each loop — `batch-loop`

`manual → set → batch → for (loop: set) → set`

Splits a list into chunks of 2 with `batch`, then the `for` node runs the body wired
to its **`loop`** handle once per chunk — `$item` and `$index` are in scope inside the
body. The collected body outputs continue on the **`done`** handle as `{items, count}`.

## 11. Poll with repeat + wait — `poll-wait-repeat`

`manual → repeat ×3 (loop: wait → http.request) → set`

Repeats the body three times: `wait` sleeps 2 seconds, then `http.request` probes an
endpoint with **Allow non-2xx** so a bad status doesn't fail the run. A minimal polling
skeleton — swap the probe URL and add an `if` on `$node.probe.output.status` to exit
early via error branching.

## 12. Python code transform — `python-transform`

`manual → code → set`

Runs arbitrary Python in the sandbox: the node's input is in scope as `input`, whatever
you `print` becomes the output (`{stdout}`). The escape hatch for transformations an
expression can't do.

## 13. Event → in-app alert — `event-inapp-alert`

`event → set → notify.inapp`

Reacts to an **internal event** (document ingested, reminder fired, run completed —
chosen in the trigger's config) and pushes a bell notification. The event payload
arrives as `$trigger`. Remember: the trigger must be **enabled** *and* the workflow
**Active** for events to fire it.

## 14. Notification broadcast (all channels) — `notify-broadcast`

`manual → set → notify.inapp + notify.telegram + notify.email + notify.webhook`

Sends one composed message to every channel in parallel. The Telegram/email/webhook
nodes use **On error → continue** so an unconfigured channel (no linked chat, no SMTP)
degrades gracefully instead of failing the run; the in-app bell always works.

## 15. Agent research brief — `agent-research-brief`

`manual → llm.agent → notify.inapp`

Hands a goal to the **durable agent loop** (full tool registry: built-in, MCP and
custom tools, up to `max_steps` iterations) and delivers its final answer
(`={{ $node.agent.output.content }}`) to the bell. Run with `{"goal": "…"}` to override
the default goal.

## 16. Centralised error alerting — `error-alert-hub`

`error → set → notify.inapp`

A watchdog workflow (fase 2.5): attach an **error trigger** to it (run panel →
＋ error; leave the watched workflow empty to react to *every* failure) and activate
it. Whenever another workflow's run fails, this one fires with
`$trigger = {workflow_id, workflow_name, run_id, error, failed_node}`, formats the
failure and pushes it to the bell. Swap `notify.inapp` for `notify.telegram`/
`notify.email` for out-of-band alerting. Loop-guarded: it never reacts to its own
failures, and error-triggered runs don't cascade.

## 17. Human approval gate — `approval-gate-deploy`

`manual → human.approval → notify.inapp (approved) + notify.inapp (rejected)`

Phase 35 (fase 4.4): the run **suspends** on the `human.approval` node (status
`waiting`, in-app notification) until someone decides from Workflow → Runs (✓ Approve /
✕ Reject, optional comment) or via `POST /approvals/{id}/decision`. Each decision routes
through its own branch with `{approved, status, comment, decided_by}` as output. The
default `timeout` is 24 h with `onTimeout: reject`. Run with `{"subject": "deploy v2"}`
to customise the request message.

## 18. Ticket triage (LLM classify) — `ticket-triage-classify`

`manual → llm.classify → switch → notify.inapp ×3` (+ `file.write` CSV log)

Phase 35 (fase 4.1 + 4.2): `llm.classify` labels the incoming text as one of
`billing | bug | question` with **guaranteed structure** (an out-of-list reply raises,
so retries apply), a `switch` on `={{ $node.triage.output.category }}` routes each
category to its queue, and every triaged ticket is appended to
`tickets/triage-log.csv` in the workspace storage via `file.write` (format `csv`,
`append: true`). Run with `{"text": "my invoice is wrong"}`.

## 19. Knowledge-base RAG answer — `kb-search-rag`

`manual → kb.search → llm.completion → set`

Phase 38 (fase 6.5): retrieval-augmented answering with the **dedicated `kb.search`
node** (not a generic agent). Searches the knowledge base for the question, then answers
**strictly from the retrieved passages** (`={{ $node.kb.output.results }}`). Run with
`{"question": "how do I configure SMTP?"}`. Needs a populated knowledge base.

## 20. Cursor pagination (while loop) — `paginate-while`

`manual → while (loop: http.request → set) → set` (+ `done`)

Phase 38 (fase 6.3): a **condition-driven `while` loop** for pagination / async-API
polling without subworkflow recursion. The `condition`
(`={{ $index == 0 or default($item.has_more, false) }}`) is re-evaluated before each
pass — `$item` is the previous body output — under a mandatory `maxIterations` cap; the
body results are collected on the **`done`** handle as `{items, count, capped}`.

## 21. Extract fields → SQL insert — `extract-to-db`

`manual → db.query (create) → llm.extract → db.query (insert) → set`

Phase 35 (fase 4.1 + 4.2): pull typed fields from free text with `llm.extract` (output
**guaranteed to match a JSON Schema**), then persist them with a **parameterised
`db.query` INSERT** into a sqlite database in workspace storage. Run with
`{"text": "Invoice #42 for ACME, 1500 EUR, due 2026-08-01"}`.

## 22. Write then read a CSV file — `file-read-report`

`manual → set → file.write → file.read → set`

Phase 35 (fase 4.2): self-contained file I/O in workspace storage — build a list of rows,
write them as CSV with `file.write`, read them back with `file.read` (parsed to
`{rows, count}`), then summarise. Every path is sandboxed inside
`GRAPH_WORKFLOW_FILES_DIR`.

## 23. Parse an HTTP JSON body in transit — `parse-http-json`

`manual → http.request → file.parse → set`

Phase 35 (fase 4.2): parse a **textual payload without touching disk** — fetch an
endpoint with `http.request`, then hand its raw text to `file.parse` (format `json` →
`{data}`). Same outputs as `file.read`, but for in-transit data like an API body or a
tool result.

## 24. Chatbot (chat trigger → reply) — `chatbot-reply`

`chat → llm.completion → chat.reply`

Phase 41 (fase 9.3): turn a workflow into a **chatbot**. A `chat` trigger feeds an LLM
and a `chat.reply` node returns the answer. Call `POST /v1/graph-workflows/{id}/chat`
with `{message, session_id?}`; the graph sees `$trigger {session_id, message, history}`
and session state persists across turns.

## 25. Run on another workflow's success — `success-pipeline`

`success → set → notify.inapp`

Phase 38 (fase 6.1): the mirror of the error trigger — a **`success` trigger** fires when
another workflow's run completes successfully (empty `config.workflow_id` = watch every
workflow), landing `$trigger {workflow_id, workflow_name, run_id, output}`. Chains
"A then B" pipelines without a subworkflow node. Loop-guarded like the error trigger.

## 26. Expense approval form — `expense-approval-form`

`manual → human.input → notify.inapp (submitted) + notify.inapp (timeout)`

Phase 42 (fase 10.1): the run **suspends** on the `human.input` node (status `waiting`,
in-app notification) until someone fills the **JSON-Schema form** (`amount` + `category`)
from Workflow → Runs or via `POST /approvals/{id}/submit`. The submitted data is
**validated against the schema** before it is accepted; the run resumes on the
**`submitted`** branch with `{data, status, comment, decided_by}` as output (`timeout`
branch otherwise). Run with `{"requester": "jane"}`.

## 27. Wait for payment confirmation — `payment-webhook-wait`

`manual → wait.event → notify.inapp (main) + notify.inapp (timeout)`

Phase 42 (fase 10.2): the run **suspends** on the `wait.event` node until an external
system (e.g. a payment provider) POSTs to `/graph-workflows/events/{order_id}`; the
delivered payload becomes the node's output on the **`main`** branch. Covers real async
callbacks (payments, signatures, tickets) without polling. Run with
`{"order_id": "ord-123"}`.

## 28. Mocked HTTP integration (test-ready) — `http-mock-pin-demo`

`manual → http.request (pinned) → set`

Phase 43 (fase 11): the `http.request` node ships with a **pinned output**
(fase 3.2), so it doubles as a ready-made playground for the new **Tests &
dry-run** panel — no live endpoint required. Open the panel and:

- **Save a test case** with `{"user_id": 42}` as the trigger payload and an
  assertion like `{"node_id": "profile", "type": "json_path", "path": "name",
  "expected": "Ada Lovelace"}`, then **Run tests** (fase 11.1) — it passes
  without ever calling `api.example.com`, because the pin stands in for the
  node.
- **Run dry-run** (fase 11.2) — the report lists `call` under *mocked external
  effects* (`source: pin`) and shows the full simulated path.
- Open **Cost estimate** (fase 11.3) — 0 LLM nodes here, so it reports "no LLM
  nodes in this graph"; swap in an `llm.completion` node and give it a
  schedule trigger to see a projected tokens/month figure instead.

Point `url` at a real endpoint and clear the pin when you are ready to go
live — a pin never affects a normal **Run now**.

---

## 29. Idempotent order processing with rollback — `idempotent-order-saga`

`webhook → state.increment → state.set (reserve) → http.request (charge) → set`,
with a `compensate` edge from *reserve* to a *release* node.

Phase 48 (fase 16): a webhook that processes each order **exactly once** and
**rolls back** on failure — the three new engine capabilities working together:

- **Idempotency (16.2)** — on the webhook trigger set a `dedupKey` of
  `{{ $trigger.order_id }}` and a `dedupWindowSeconds` (e.g. `3600`). If the
  sender retries the same order within the window, the hook returns the original
  `run_id` with `deduped: true` instead of charging twice.
- **Persistent state (16.1)** — `state.increment` on key `orders_processed`
  keeps a running counter **across runs**; `state.set reserved:{{ $trigger.order_id }}`
  records the reservation. Inspect or edit both from the run panel
  (`GET /v1/graph-workflows/{id}/state`).
- **Compensation / saga (16.3)** — the *reserve* node wires a `compensate` edge
  to a *release* node. If *charge* (or anything after *reserve*) fails, the
  engine walks back and runs *release*, seeded with *reserve*'s output, so the
  stock is freed. The run still ends `failed`, but the side effect is undone.

Add a **priority (16.4)** on the trigger config (`"priority": 10`) to let urgent
orders jump ahead of a batch backfill in the per-workflow queue.

---

See [Visual workflows](../en/visual-workflows.md) for the full feature guide, and
[roadmap.md](../roadmap.md) Phase 29 for the design.
