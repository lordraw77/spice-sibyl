# Example workflows (Phase 24.a)

A curated set of ready-to-run [Phase 18](../roadmap.md) workflow definitions ships
with the repo. They appear in the **Examples** gallery on the `/workflows` page and
import with one click: importing **pre-fills the create form** (goal, extra
instructions, max steps). Nothing runs on import — you still pick a model and press
**Start run**, so you stay in control of cost and provider.

Each example declares the built-in tools it exercises. A CI smoke test
(`backend/tests/test_phase24.py`) asserts every declared tool is still registered
in `app/tools/registry.py`, so a tool rename can never silently strand a one-click
example. The examples themselves live in
[`backend/app/examples/workflow_examples.py`](../../backend/app/examples/workflow_examples.py)
and are served read-only from `GET /v1/workflows/examples`.

## How to use

1. Open **/workflows** and click **Browse examples**.
2. Pick a card and press **Import** — the form above is filled in.
3. Choose a model and press **Start run**.
4. Watch it progress step by step; pause/resume/inspect as usual.

Some examples need a linked Telegram account (for `create_reminder` delivery) or a
populated knowledge base (for `kb_search`). Where a prerequisite is missing the run
will report it rather than fabricate output — the example prompts instruct the model
to do exactly that.

---

## 1. Morning news digest

**Tools:** `fetch_rss`, `create_reminder` · **id:** `morning-news-digest`

Pulls the latest items from an RSS/Atom feed, writes a tight bullet summary, and
schedules it as a Telegram reminder so the digest lands in your chat.

- **fetch_rss** retrieves the 5 most recent entries from `https://hnrss.org/frontpage`.
- The model summarises each entry to a one-line takeaway and assembles a Markdown digest.
- **create_reminder** (`when: '+2m'`) delivers the digest to your linked Telegram.

> Prerequisite: a linked Telegram account for delivery. Swap the feed URL for your own.

## 2. Website watcher

**Tools:** `read_url`, `create_reminder` · **id:** `website-watcher`

Reads a web page, extracts what matters, and pings you on Telegram with the finding.

- **read_url** fetches `https://news.ycombinator.com/` and the model extracts the top 3
  stories with their points.
- The model condenses "what's trending" into one sentence.
- **create_reminder** (`when: '+2m'`) notifies you with the summary and titles.

> For true change detection across runs, combine with `search_conversations` or a
> scheduled run (Phase 27) so the previous snapshot is available to diff against.

## 3. KB research report

**Tools:** `kb_search`, `python_exec` · **id:** `kb-research-report`

Searches your personal knowledge base, analyses the passages, draws a chart with the
sandboxed Python interpreter, and exports a Markdown report.

- **kb_search** retrieves the most relevant passages from your uploaded documents.
- The model identifies 3–5 key themes and their mention frequency, grounded in the passages.
- **python_exec** renders a matplotlib bar chart and saves it as `chart.png`.
- The model produces a Markdown report (executive summary, themes, chart reference).

> Prerequisite: a non-empty knowledge base. With no documents the run says so and stops
> rather than inventing content.

## 4. Weather-aware reminder

**Tools:** `get_weather`, `create_reminder` · **id:** `weather-aware-reminder`

Checks the forecast and sets a Telegram reminder only when the weather calls for it.

- **get_weather** fetches today's forecast for Milano (Open-Meteo, no API key).
- If rain is likely, **create_reminder** sets a 07:30 reminder to take an umbrella,
  including the expected conditions; on a clear day no reminder is created.
- The model reports the decision and the precipitation figure it used.

> Prerequisite: a linked Telegram account. Change the city in the goal to your own.

---

## Adding a new example

1. Append an entry to `WORKFLOW_EXAMPLES` in
   [`backend/app/examples/workflow_examples.py`](../../backend/app/examples/workflow_examples.py)
   with a stable, unique `id`, a clear `goal`, and the `required_tools` it calls.
2. `required_tools` names must match `app/tools/registry.py` — the smoke test enforces this.
3. Add a section here documenting it step by step.
4. Run the smoke test: `pytest tests/test_phase24.py` (see `backend` test notes).

The gallery updates automatically — the frontend renders whatever the endpoint returns.
