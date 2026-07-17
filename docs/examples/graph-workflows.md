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

---

## 1. RSS morning digest — `rss-morning-digest`

`schedule → tool.fetch_rss → llm.completion → set`

On a schedule, pulls the latest entries from an RSS/Atom feed
(`https://hnrss.org/frontpage`), has an LLM condense them into five bullets, then
builds a titled digest object. Shows a trigger → action → AI → data-mapping chain and
an expression that pipes one node's output into the next
(`={{ $node.rss.output.result }}`).

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

---

See [Visual workflows](../en/visual-workflows.md) for the full feature guide, and
[roadmap.md](../roadmap.md) Phase 29 for the design.
