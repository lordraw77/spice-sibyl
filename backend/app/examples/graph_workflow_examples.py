"""
Phase 29 — curated, importable *graph* workflow examples.

Where ``workflow_examples`` (Phase 24.a) are agent-run goals, these are ready-made
**node graphs** for the visual engine. The ``/graph-workflows`` editor imports one
with a click (it POSTs the ``graph`` as a new workflow). Each example only uses node
types the engine ships and tools present in ``app.tools.registry`` — a CI smoke test
(``tests/test_phase29.py``) asserts that stays true, catching a rename before a user
hits it.

Keep the ``id`` values stable — they are referenced by docs/examples/graph-workflows.md
and may be logged. Data only, no behaviour.
"""

from __future__ import annotations

# Each entry: id/title/description/category (presentation) + node_types (the tool
# nodes it needs, for "needs:" chips + CI) + graph ({nodes, edges}) matching the
# WorkflowGraph shape.
GRAPH_WORKFLOW_EXAMPLES: list[dict] = [
    {
        "id": "rss-morning-digest",
        "title": "RSS morning digest",
        "description": (
            "On a schedule, pull the latest headlines from an RSS/Atom feed, have an "
            "LLM condense them into five bullets, and assemble a titled digest."
        ),
        "category": "news",
        "node_types": ["schedule", "tool.fetch_rss", "llm.completion", "set"],
        "graph": {
            "nodes": [
                {"id": "trigger", "type": "schedule", "name": "Every morning",
                 "position": {"x": 40, "y": 80}},
                {"id": "rss", "type": "tool.fetch_rss", "name": "HN frontpage",
                 "params": {"url": "https://hnrss.org/frontpage", "max_entries": 5},
                 "position": {"x": 280, "y": 80}},
                {"id": "summary", "type": "llm.completion", "name": "Summarize",
                 "params": {
                     "system": "You are a concise news editor. Five bullets, one line each, no fluff.",
                     "prompt": "Summarize these RSS entries into five tight bullets:\n\n={{ $node.rss.output.result }}",
                 },
                 "position": {"x": 540, "y": 80}},
                {"id": "digest", "type": "set", "name": "Digest",
                 "params": {"fields": {
                     "title": "Morning digest",
                     "body": "={{ $node.summary.output.content }}",
                 }},
                 "position": {"x": 800, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger", "target": "rss"},
                {"id": "e2", "source": "rss", "target": "summary"},
                {"id": "e3", "source": "summary", "target": "digest"},
            ],
        },
    },
    {
        "id": "weather-greeting",
        "title": "Weather-aware greeting",
        "description": (
            "Run on demand: fetch the current weather for a city and build a friendly "
            "greeting message that embeds it — a minimal tool → data-mapping flow."
        ),
        "category": "utility",
        "node_types": ["manual", "tool.get_weather", "set"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Run",
                 "position": {"x": 40, "y": 80}},
                {"id": "weather", "type": "tool.get_weather", "name": "Weather",
                 "params": {"location": "Milano, Italy", "days": 1},
                 "position": {"x": 280, "y": 80}},
                {"id": "message", "type": "set", "name": "Greeting",
                 "params": {"fields": {
                     "message": "Good morning! Here is the weather in Milano:\n={{ $node.weather.output.result }}",
                 }},
                 "position": {"x": 540, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "weather"},
                {"id": "e2", "source": "weather", "target": "message"},
            ],
        },
    },
    {
        "id": "webhook-kb-answer",
        "title": "Webhook → knowledge-base answer",
        "description": (
            "Expose a webhook that takes a question, searches your knowledge base, and "
            "has an LLM answer strictly from the retrieved passages."
        ),
        "category": "automation",
        "node_types": ["webhook", "tool.kb_search", "llm.completion", "set"],
        "graph": {
            "nodes": [
                {"id": "hook", "type": "webhook", "name": "Ask (POST)",
                 "position": {"x": 40, "y": 80}},
                {"id": "kb", "type": "tool.kb_search", "name": "KB search",
                 "params": {"query": "={{ $trigger.question }}", "top_k": 4},
                 "position": {"x": 280, "y": 80}},
                {"id": "answer", "type": "llm.completion", "name": "Answer",
                 "params": {
                     "system": "Answer only from the provided context. If it isn't there, say you don't know.",
                     "prompt": "Question: ={{ $trigger.question }}\n\nContext:\n={{ $node.kb.output.result }}",
                 },
                 "position": {"x": 540, "y": 80}},
                {"id": "out", "type": "set", "name": "Response",
                 "params": {"fields": {"answer": "={{ $node.answer.output.content }}"}},
                 "position": {"x": 800, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "hook", "target": "kb"},
                {"id": "e2", "source": "kb", "target": "answer"},
                {"id": "e3", "source": "answer", "target": "out"},
            ],
        },
    },
    {
        "id": "page-keyword-watch",
        "title": "Page keyword watcher (branching)",
        "description": (
            "On a schedule, fetch a web page and branch on whether a keyword appears: "
            "one branch raises an alert, the other records 'no change'. Shows if-routing "
            "and a whitelisted expression."
        ),
        "category": "monitoring",
        "node_types": ["schedule", "tool.read_url", "if", "set"],
        "graph": {
            "nodes": [
                {"id": "cron", "type": "schedule", "name": "Hourly",
                 "position": {"x": 40, "y": 120}},
                {"id": "fetch", "type": "tool.read_url", "name": "Fetch page",
                 "params": {"url": "https://example.com", "max_chars": 2000},
                 "position": {"x": 260, "y": 120}},
                {"id": "check", "type": "if", "name": "Contains 'sale'?",
                 "params": {"condition": "={{ 'sale' in lower($node.fetch.output.result) }}"},
                 "position": {"x": 500, "y": 120}},
                {"id": "alert", "type": "set", "name": "Alert",
                 "params": {"fields": {"status": "changed", "note": "Keyword found on the page"}},
                 "position": {"x": 720, "y": 50}},
                {"id": "noop", "type": "set", "name": "No change",
                 "params": {"fields": {"status": "unchanged"}},
                 "position": {"x": 720, "y": 200}},
            ],
            "edges": [
                {"id": "e1", "source": "cron", "target": "fetch"},
                {"id": "e2", "source": "fetch", "target": "check"},
                {"id": "e3", "source": "check", "target": "alert", "sourceHandle": "true"},
                {"id": "e4", "source": "check", "target": "noop", "sourceHandle": "false"},
            ],
        },
    },
]


def list_graph_workflow_examples() -> list[dict]:
    """The curated graph-workflow examples (read-only)."""
    return GRAPH_WORKFLOW_EXAMPLES
