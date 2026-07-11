# Example graph workflows (Phase 29)

A curated set of ready-to-run **visual node-graph** workflows ships with the repo.
They appear in the **Examples** gallery on the `/graph-workflows` page (the ✨ button)
and import with one click: importing **creates a new workflow from the example's
graph**, then opens it in the editor so you can inspect, tweak, and **Run now**.

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

---

See [Visual workflows](../en/visual-workflows.md) for the full feature guide, and
[roadmap.md](../roadmap.md) Phase 29 for the design.
