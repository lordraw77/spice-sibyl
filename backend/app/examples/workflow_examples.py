"""
Phase 24.a — curated, ready-to-run workflow (agent run) definitions.

A workflow in Phase 18 is just a goal + system prompt + step budget handed to the
durable tool loop. An *example* is a pre-written definition that the ``/workflows``
page can import with one click. Each declares the built-in tools it exercises so:

  * the UI can render "needs: fetch_rss, create_reminder" chips, and
  * a CI smoke test can assert every declared tool is still registered
    (``tests/test_phase24.py``), catching a rename before it reaches a user.

Keep the ``id`` values stable — they are referenced by docs/examples/workflows.md
and may be logged. ``goal`` is the user-visible task; ``system_prompt`` maps to the
run's extra instructions. Tools listed in ``required_tools`` must match the names in
``app.tools.registry.TOOL_DEFINITIONS``.
"""

from __future__ import annotations

# Each entry mirrors the AgentRunCreate shape (goal / system_prompt / max_steps)
# plus presentation + provenance metadata. Data only — no behaviour here.
WORKFLOW_EXAMPLES: list[dict] = [
    {
        "id": "morning-news-digest",
        "title": "Morning news digest",
        "description": (
            "Pulls the latest items from an RSS/Atom feed, writes a tight bullet "
            "summary, and schedules it as a Telegram reminder so it lands in your "
            "chat."
        ),
        "category": "news",
        "required_tools": ["fetch_rss", "create_reminder"],
        "max_steps": 12,
        "goal": (
            "Fetch the 5 most recent entries from the feed "
            "https://hnrss.org/frontpage . For each, give the title and a one-line "
            "takeaway. Then assemble a short Markdown digest titled 'Morning "
            "digest' and create a reminder that delivers this digest to me on "
            "Telegram in 2 minutes (when: '+2m'). Confirm the reminder was created."
        ),
        "system_prompt": (
            "Be concise. Summaries are one line each, no fluff. If the feed cannot "
            "be fetched, say so plainly and do not invent headlines. Put the whole "
            "digest into the reminder text."
        ),
    },
    {
        "id": "website-watcher",
        "title": "Website watcher",
        "description": (
            "Reads a web page, extracts the parts that matter, and flags anything "
            "noteworthy — then pings you on Telegram with the finding."
        ),
        "category": "monitoring",
        "required_tools": ["read_url", "create_reminder"],
        "max_steps": 12,
        "goal": (
            "Read the page https://news.ycombinator.com/ and extract the current "
            "top 3 story titles with their points. Summarise what's trending in one "
            "sentence. Then create a Telegram reminder (when: '+2m') whose text is "
            "that one-sentence summary plus the 3 titles, so I get notified. "
            "Confirm the reminder id."
        ),
        "system_prompt": (
            "Only report what is actually on the page — never fabricate entries. If "
            "the page is unavailable, report the failure instead of guessing."
        ),
    },
    {
        "id": "kb-research-report",
        "title": "KB research report",
        "description": (
            "Searches your personal knowledge base, analyses the passages, draws a "
            "chart with the sandboxed Python interpreter, and exports a Markdown "
            "report."
        ),
        "category": "research",
        "required_tools": ["kb_search", "python_exec"],
        "max_steps": 16,
        "goal": (
            "Search my knowledge base for the most relevant passages about the main "
            "topic of my documents. From what you find, identify 3-5 key themes and "
            "how often each is mentioned. Use python_exec to render a simple bar "
            "chart (matplotlib) of theme frequency and save it as chart.png. "
            "Finally produce a Markdown research report with an executive summary, "
            "the themes, and a reference to the chart."
        ),
        "system_prompt": (
            "Ground every claim in retrieved passages; if the knowledge base is "
            "empty or returns nothing, say so and stop rather than inventing "
            "content. Keep the Python code small and print what it does."
        ),
    },
    {
        "id": "weather-aware-reminder",
        "title": "Weather-aware reminder",
        "description": (
            "Checks the forecast for your city and sets a Telegram reminder only "
            "when the weather calls for it — e.g. 'take an umbrella'."
        ),
        "category": "assistant",
        "required_tools": ["get_weather", "create_reminder"],
        "max_steps": 10,
        "goal": (
            "Get today's weather forecast for Milano. If rain or precipitation is "
            "likely, create a Telegram reminder for 07:30 tomorrow saying to take "
            "an umbrella (include the expected conditions). If the day looks clear, "
            "do not create a reminder and just tell me it's not needed. Report what "
            "you decided and why."
        ),
        "system_prompt": (
            "Base the decision strictly on the forecast returned by get_weather. "
            "State the precipitation figure you used. Create at most one reminder."
        ),
    },
]


def list_workflow_examples() -> list[dict]:
    """Return the curated workflow examples (stable order)."""
    return WORKFLOW_EXAMPLES
