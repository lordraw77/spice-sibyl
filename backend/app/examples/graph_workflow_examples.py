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
        "id": "llm-quality-gate",
        "title": "LLM quality gate (generate → judge)",
        "description": (
            "Generate a draft with an LLM, then have 'llm.judge' score it against a rubric "
            "(1..5). Scores at or above the threshold take the 'pass' handle and publish; "
            "lower scores take 'fail' and get flagged for review instead of shipping. Wire "
            "the 'fail' handle back into the generator (with 'while') for a regenerate loop."
        ),
        "category": "ai",
        "node_types": ["manual", "llm.completion", "llm.judge", "set"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Run",
                 "position": {"x": 40, "y": 120}},
                {"id": "draft", "type": "llm.completion", "name": "Write draft",
                 "params": {
                     "system": "You are a marketing copywriter. Write a short product blurb.",
                     "prompt": "Write a 2-sentence blurb for: ={{ $trigger.topic }}",
                 },
                 "position": {"x": 280, "y": 120}},
                {"id": "judge", "type": "llm.judge", "name": "Judge quality",
                 "params": {
                     "input": "={{ $node.draft.output.content }}",
                     "criteria": "Clear, specific, no clichés, compelling call to action.",
                     "scaleMax": 5,
                     "threshold": 4,
                 },
                 "position": {"x": 540, "y": 120}},
                {"id": "publish", "type": "set", "name": "Publish",
                 "params": {"fields": {"status": "published", "copy": "={{ $node.draft.output.content }}"}},
                 "position": {"x": 800, "y": 40}},
                {"id": "review", "type": "set", "name": "Flag for review",
                 "params": {"fields": {
                     "status": "needs_review",
                     "reason": "={{ $node.judge.output.rationale }}",
                     "score": "={{ $node.judge.output.score }}",
                 }},
                 "position": {"x": 800, "y": 220}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "draft"},
                {"id": "e2", "source": "draft", "target": "judge"},
                {"id": "e3", "source": "judge", "sourceHandle": "pass", "target": "publish"},
                {"id": "e4", "source": "judge", "sourceHandle": "fail", "target": "review"},
            ],
        },
    },
    {
        "id": "rss-to-slack-alert",
        "title": "RSS → LLM → Slack (Phase 47)",
        "description": (
            "Attach an 'rss.read' trigger (poll a feed, one run per new entry, deduped "
            "by guid): the $trigger carries {title, link, published, summary, guid}. An "
            "LLM writes a one-line take, then the curated Slack connector posts it — no "
            "hand-built HTTP call, token pulled from $secrets."
        ),
        "category": "news",
        "node_types": ["rss.read", "llm.completion", "connector.slack.postMessage"],
        "graph": {
            "nodes": [
                {"id": "feed", "type": "rss.read", "name": "New feed entry",
                 "position": {"x": 40, "y": 80}},
                {"id": "take", "type": "llm.completion", "name": "One-line take",
                 "params": {
                     "system": "You are a wry tech newsletter editor. One sentence, no emoji.",
                     "prompt": "Write a one-line reaction to this headline:\n={{ $trigger.title }}\n\n={{ $trigger.summary }}",
                 },
                 "position": {"x": 300, "y": 80}},
                {"id": "post", "type": "connector.slack.postMessage", "name": "Post to Slack",
                 "params": {
                     "token": "={{ $secrets.SLACK_TOKEN }}",
                     "channel": "#news",
                     "text": "*={{ $trigger.title }}*\n={{ $node.take.output.content }}\n={{ $trigger.link }}",
                 },
                 "position": {"x": 560, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "feed", "target": "take"},
                {"id": "e2", "source": "take", "target": "post"},
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
    {
        "id": "api-error-fallback",
        "title": "API call with error fallback",
        "description": (
            "Call an external HTTP API with two retries; on success shape the response, "
            "on failure route through the node's error branch to build an alert instead "
            "of failing the run. Shows http.request + onError='branch'."
        ),
        "category": "automation",
        "node_types": ["manual", "http.request", "set"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Run",
                 "position": {"x": 40, "y": 120}},
                {"id": "api", "type": "http.request", "name": "GET status",
                 "params": {"method": "GET", "url": "https://example.com", "timeout": 15},
                 "retry": 2, "backoff": 1.0, "onError": "branch",
                 "position": {"x": 280, "y": 120}},
                {"id": "ok", "type": "set", "name": "Shape response",
                 "params": {"fields": {
                     "status": "={{ $node.api.output.status }}",
                     "body": "={{ $node.api.output.text }}",
                 }},
                 "position": {"x": 540, "y": 50}},
                {"id": "alert", "type": "set", "name": "Alert",
                 "params": {"fields": {
                     "status": "api_down",
                     "detail": "={{ $node.api.output.error }}",
                 }},
                 "position": {"x": 540, "y": 200}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "api"},
                {"id": "e2", "source": "api", "target": "ok", "sourceHandle": "main"},
                {"id": "e3", "source": "api", "target": "alert", "sourceHandle": "error"},
            ],
        },
    },
    {
        "id": "subworkflow-composer",
        "title": "Compose workflows (subworkflow)",
        "description": (
            "Run another workflow as a child step and post-process its result. Pick the "
            "child workflow from the node's dropdown in the inspector; its sink output "
            "comes back as $node.child.output.output."
        ),
        "category": "automation",
        "node_types": ["manual", "subworkflow", "set"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Run",
                 "position": {"x": 40, "y": 80}},
                {"id": "child", "type": "subworkflow", "name": "Child workflow",
                 "params": {
                     "workflow_id": "",
                     "payload": {"input": "={{ $trigger.input }}"},
                 },
                 "position": {"x": 280, "y": 80}},
                {"id": "wrap", "type": "set", "name": "Wrap result",
                 "params": {"fields": {
                     "child_run": "={{ $node.child.output.run_id }}",
                     "result": "={{ $node.child.output.output }}",
                 }},
                 "position": {"x": 540, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "child"},
                {"id": "e2", "source": "child", "target": "wrap"},
            ],
        },
    },
    {
        "id": "switch-routing",
        "title": "Switch routing (multi-branch)",
        "description": (
            "Route the run to one of three branches based on a value: switch matches "
            "the resolved value against its cases and falls back to 'default'. "
            "Run it with a payload like {\"channel\": \"a\"} to steer the branch."
        ),
        "category": "logic",
        "node_types": ["manual", "switch", "set"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Run",
                 "position": {"x": 40, "y": 120}},
                {"id": "route", "type": "switch", "name": "Route by channel",
                 "params": {"value": "={{ default($trigger.channel, 'a') }}", "cases": ["a", "b"]},
                 "position": {"x": 280, "y": 120}},
                {"id": "brancha", "type": "set", "name": "Branch A",
                 "params": {"fields": {"route": "alpha"}},
                 "position": {"x": 540, "y": 30}},
                {"id": "branchb", "type": "set", "name": "Branch B",
                 "params": {"fields": {"route": "bravo"}},
                 "position": {"x": 540, "y": 120}},
                {"id": "fallback", "type": "set", "name": "Fallback",
                 "params": {"fields": {"route": "default"}},
                 "position": {"x": 540, "y": 210}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "route"},
                {"id": "e2", "source": "route", "target": "brancha", "sourceHandle": "case:a"},
                {"id": "e3", "source": "route", "target": "branchb", "sourceHandle": "case:b"},
                {"id": "e4", "source": "route", "target": "fallback", "sourceHandle": "default"},
            ],
        },
    },
    {
        "id": "fanout-merge",
        "title": "Parallel fan-out + merge",
        "description": (
            "Fan a trigger out to two parallel branches, collect both results with a "
            "merge node (output {items}), then reduce them with aggregate (sum)."
        ),
        "category": "logic",
        "node_types": ["manual", "set", "merge", "aggregate"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Run",
                 "position": {"x": 40, "y": 120}},
                {"id": "left", "type": "set", "name": "Source A",
                 "params": {"fields": {"source": "a", "value": 1}},
                 "position": {"x": 280, "y": 50}},
                {"id": "right", "type": "set", "name": "Source B",
                 "params": {"fields": {"source": "b", "value": 2}},
                 "position": {"x": 280, "y": 200}},
                {"id": "join", "type": "merge", "name": "Merge",
                 "position": {"x": 540, "y": 120}},
                {"id": "total", "type": "aggregate", "name": "Sum values",
                 "params": {"items": "={{ $node.join.output.items }}", "op": "sum", "field": "value"},
                 "position": {"x": 800, "y": 120}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "left"},
                {"id": "e2", "source": "start", "target": "right"},
                {"id": "e3", "source": "left", "target": "join"},
                {"id": "e4", "source": "right", "target": "join"},
                {"id": "e5", "source": "join", "target": "total"},
            ],
        },
    },
    {
        "id": "orders-filter-total",
        "title": "Data pipeline: filter + aggregate",
        "description": (
            "Build a list of orders, keep only the large ones with a filter (the keep "
            "mask uses the =py: escape hatch), then sum their totals with aggregate."
        ),
        "category": "data",
        "node_types": ["manual", "set", "filter", "aggregate"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Run",
                 "position": {"x": 40, "y": 80}},
                {"id": "data", "type": "set", "name": "Orders",
                 "params": {"fields": {
                     "orders": "={{ [{'sku': 'A', 'total': 120}, {'sku': 'B', 'total': 40}, {'sku': 'C', 'total': 310}] }}",
                 }},
                 "position": {"x": 260, "y": 80}},
                {"id": "big", "type": "filter", "name": "Only > 100",
                 "params": {
                     "items": "={{ $node.data.output.orders }}",
                     "keep": "=py:[o['total'] > 100 for o in node['data']['output']['orders']]",
                 },
                 "position": {"x": 500, "y": 80}},
                {"id": "sum", "type": "aggregate", "name": "Revenue",
                 "params": {"items": "={{ $node.big.output.items }}", "op": "sum", "field": "total"},
                 "position": {"x": 740, "y": 80}},
                {"id": "report", "type": "set", "name": "Report",
                 "params": {"fields": {
                     "kept": "={{ $node.big.output.count }}",
                     "revenue": "={{ $node.sum.output.result }}",
                 }},
                 "position": {"x": 980, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "data"},
                {"id": "e2", "source": "data", "target": "big"},
                {"id": "e3", "source": "big", "target": "sum"},
                {"id": "e4", "source": "sum", "target": "report"},
            ],
        },
    },
    {
        "id": "batch-loop",
        "title": "Batch + for-each loop",
        "description": (
            "Split a list into chunks with batch, then run the loop body once per "
            "chunk ($item / $index in scope). The for node collects the body outputs "
            "and continues on 'done' with {items, count}."
        ),
        "category": "data",
        "node_types": ["manual", "set", "batch", "for"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Run",
                 "position": {"x": 40, "y": 80}},
                {"id": "data", "type": "set", "name": "Items",
                 "params": {"fields": {"items": "={{ ['alpha', 'bravo', 'charlie', 'delta', 'echo'] }}"}},
                 "position": {"x": 260, "y": 80}},
                {"id": "chunk", "type": "batch", "name": "Chunks of 2",
                 "params": {"items": "={{ $node.data.output.items }}", "size": 2},
                 "position": {"x": 500, "y": 80}},
                {"id": "each", "type": "for", "name": "Per chunk",
                 "params": {"items": "={{ $node.chunk.output.batches }}"},
                 "position": {"x": 740, "y": 80}},
                {"id": "label", "type": "set", "name": "Body: label chunk",
                 "params": {"fields": {
                     "n": "={{ $index }}",
                     "chunk": "={{ $item }}",
                     "size": "={{ len($item) }}",
                 }},
                 "position": {"x": 740, "y": 230}},
                {"id": "summary", "type": "set", "name": "Summary",
                 "params": {"fields": {"processed": "={{ $node.each.output.count }}"}},
                 "position": {"x": 980, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "data"},
                {"id": "e2", "source": "data", "target": "chunk"},
                {"id": "e3", "source": "chunk", "target": "each"},
                {"id": "e4", "source": "each", "target": "label", "sourceHandle": "loop"},
                {"id": "e5", "source": "each", "target": "summary", "sourceHandle": "done"},
            ],
        },
    },
    {
        "id": "poll-wait-repeat",
        "title": "Poll with repeat + wait",
        "description": (
            "Repeat the body 3 times: wait a couple of seconds, then probe an HTTP "
            "endpoint (allow_errors keeps non-2xx from failing the run). The body "
            "results are collected on 'done' — a minimal polling skeleton."
        ),
        "category": "monitoring",
        "node_types": ["manual", "repeat", "wait", "http.request", "set"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Run",
                 "position": {"x": 40, "y": 80}},
                {"id": "loop", "type": "repeat", "name": "3 attempts",
                 "params": {"times": 3},
                 "position": {"x": 260, "y": 80}},
                {"id": "pause", "type": "wait", "name": "Body: wait 2s",
                 "params": {"seconds": 2},
                 "position": {"x": 260, "y": 230}},
                {"id": "probe", "type": "http.request", "name": "Body: probe",
                 "params": {"method": "GET", "url": "https://example.com", "timeout": 10,
                            "allow_errors": "true"},
                 "position": {"x": 500, "y": 230}},
                {"id": "report", "type": "set", "name": "Report",
                 "params": {"fields": {"attempts": "={{ $node.loop.output.count }}"}},
                 "position": {"x": 500, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "loop"},
                {"id": "e2", "source": "loop", "target": "pause", "sourceHandle": "loop"},
                {"id": "e3", "source": "pause", "target": "probe"},
                {"id": "e4", "source": "loop", "target": "report", "sourceHandle": "done"},
            ],
        },
    },
    {
        "id": "python-transform",
        "title": "Python code transform",
        "description": (
            "Run arbitrary Python in the sandbox: the node's input is in scope as "
            "`input`, prints become the output ({stdout}). Handy when an expression "
            "isn't enough."
        ),
        "category": "data",
        "node_types": ["manual", "code", "set"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Run",
                 "position": {"x": 40, "y": 80}},
                {"id": "py", "type": "code", "name": "Word count",
                 "params": {"code": (
                     "import json\n"
                     "text = (input or {}).get('text', 'hello brave new world')\n"
                     "words = text.split()\n"
                     "print(json.dumps({'words': len(words), 'longest': max(words, key=len)}))"
                 )},
                 "position": {"x": 280, "y": 80}},
                {"id": "out", "type": "set", "name": "Result",
                 "params": {"fields": {"report": "={{ $node.py.output.stdout }}"}},
                 "position": {"x": 540, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "py"},
                {"id": "e2", "source": "py", "target": "out"},
            ],
        },
    },
    {
        "id": "event-inapp-alert",
        "title": "Event → in-app alert",
        "description": (
            "React to an internal event (document ingested, reminder fired, run "
            "completed — pick it in the trigger config) and push a bell notification. "
            "The event payload arrives as $trigger."
        ),
        "category": "automation",
        "node_types": ["event", "set", "notify.inapp"],
        "graph": {
            "nodes": [
                {"id": "evt", "type": "event", "name": "On event",
                 "position": {"x": 40, "y": 80}},
                {"id": "msg", "type": "set", "name": "Compose",
                 "params": {"fields": {"body": "={{ str($trigger) }}"}},
                 "position": {"x": 280, "y": 80}},
                {"id": "bell", "type": "notify.inapp", "name": "Bell",
                 "params": {"title": "Workflow event", "body": "={{ $node.msg.output.body }}"},
                 "position": {"x": 540, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "evt", "target": "msg"},
                {"id": "e2", "source": "msg", "target": "bell"},
            ],
        },
    },
    {
        "id": "notify-broadcast",
        "title": "Notification broadcast (all channels)",
        "description": (
            "Send one message to every channel at once: in-app bell, Telegram, email "
            "and an outgoing webhook. Unconfigured channels continue on their error "
            "branch-less path (onError='continue') instead of failing the run."
        ),
        "category": "notify",
        "node_types": ["manual", "set", "notify.inapp", "notify.telegram", "notify.email", "notify.webhook"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Run",
                 "position": {"x": 40, "y": 160}},
                {"id": "msg", "type": "set", "name": "Compose",
                 "params": {"fields": {
                     "subject": "Spice Sibyl alert",
                     "text": "={{ default($trigger.message, 'Test broadcast from the visual workflow engine') }}",
                 }},
                 "position": {"x": 260, "y": 160}},
                {"id": "bell", "type": "notify.inapp", "name": "In-app",
                 "params": {"title": "Broadcast", "body": "={{ $node.msg.output.text }}"},
                 "position": {"x": 520, "y": 30}},
                {"id": "tg", "type": "notify.telegram", "name": "Telegram",
                 "params": {"text": "={{ $node.msg.output.text }}"},
                 "onError": "continue",
                 "position": {"x": 520, "y": 120}},
                {"id": "mail", "type": "notify.email", "name": "Email",
                 "params": {
                     "to": "me@example.com",
                     "subject": "={{ $node.msg.output.subject }}",
                     "body": "={{ $node.msg.output.text }}",
                 },
                 "onError": "continue",
                 "position": {"x": 520, "y": 210}},
                {"id": "hook", "type": "notify.webhook", "name": "Webhook out",
                 "params": {"url": "https://example.com/webhook"},
                 "onError": "continue",
                 "position": {"x": 520, "y": 300}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "msg"},
                {"id": "e2", "source": "msg", "target": "bell"},
                {"id": "e3", "source": "msg", "target": "tg"},
                {"id": "e4", "source": "msg", "target": "mail"},
                {"id": "e5", "source": "msg", "target": "hook"},
            ],
        },
    },
    {
        "id": "agent-research-brief",
        "title": "Agent research brief",
        "description": (
            "Hand a goal to the durable agent loop (full tool registry: built-in, MCP "
            "and custom tools) and push its final answer to the bell. Run it with a "
            "payload like {\"goal\": \"...\"} to override the default goal."
        ),
        "category": "ai",
        "node_types": ["manual", "llm.agent", "notify.inapp"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Run",
                 "position": {"x": 40, "y": 80}},
                {"id": "agent", "type": "llm.agent", "name": "Research agent",
                 "params": {
                     "goal": "={{ default($trigger.goal, 'Find the top three trending topics in AI this week and summarize each in two sentences.') }}",
                     "max_steps": 8,
                 },
                 "position": {"x": 280, "y": 80}},
                {"id": "bell", "type": "notify.inapp", "name": "Deliver",
                 "params": {"title": "Agent brief", "body": "={{ $node.agent.output.content }}"},
                 "position": {"x": 540, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "agent"},
                {"id": "e2", "source": "agent", "target": "bell"},
            ],
        },
    },
    {
        "id": "error-alert-hub",
        "title": "Centralised error alerting",
        "description": (
            "Phase 33 (fase 2.5) — a watchdog workflow that fires whenever another "
            "workflow's run fails: attach an 'error' trigger (run panel → ＋ error, "
            "empty = watch every workflow), activate it, and each failure lands here "
            "as $trigger {workflow_id, workflow_name, run_id, error, failed_node}, "
            "formatted and pushed to the in-app bell."
        ),
        "category": "notify",
        "node_types": ["error", "set", "notify.inapp"],
        "graph": {
            "nodes": [
                {"id": "err", "type": "error", "name": "On workflow error",
                 "position": {"x": 40, "y": 80}},
                {"id": "msg", "type": "set", "name": "Format alert",
                 "params": {"fields": {
                     "title": "Workflow failed: ={{ default($trigger.workflow_name, $trigger.workflow_id) }}",
                     "text": "Run ={{ $trigger.run_id }} failed at node '={{ $trigger.failed_node }}': ={{ $trigger.error }}",
                 }},
                 "position": {"x": 280, "y": 80}},
                {"id": "bell", "type": "notify.inapp", "name": "Alert",
                 "params": {"title": "={{ $node.msg.output.title }}", "body": "={{ $node.msg.output.text }}"},
                 "position": {"x": 540, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "err", "target": "msg"},
                {"id": "e2", "source": "msg", "target": "bell"},
            ],
        },
    },
    {
        "id": "approval-gate-deploy",
        "title": "Human approval gate",
        "description": (
            "Phase 35 (fase 4.4) — the run suspends on a human.approval node (status "
            "'waiting', in-app notification) until someone approves or rejects it from "
            "Workflow → Runs; each decision routes through its own branch. Run it with "
            "a payload like {\"subject\": \"deploy v2\"}."
        ),
        "category": "ops",
        "node_types": ["manual", "human.approval", "notify.inapp"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Request",
                 "position": {"x": 40, "y": 80}},
                {"id": "gate", "type": "human.approval", "name": "Wait for approval",
                 "params": {
                     "title": "Approval requested",
                     "message": "Approve '={{ default($trigger.subject, 'this request') }}'?",
                     "timeout": 86400,
                     "onTimeout": "reject",
                 },
                 "position": {"x": 280, "y": 80}},
                {"id": "go", "type": "notify.inapp", "name": "Approved",
                 "params": {"title": "Approved ✅",
                            "body": "={{ default($node.gate.output.comment, 'Request approved.') }}"},
                 "position": {"x": 540, "y": 30}},
                {"id": "stop", "type": "notify.inapp", "name": "Rejected",
                 "params": {"title": "Rejected ❌",
                            "body": "Request ={{ $node.gate.output.status }}: ={{ default($node.gate.output.comment, 'no comment') }}"},
                 "position": {"x": 540, "y": 160}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "gate"},
                {"id": "e2", "source": "gate", "target": "go", "sourceHandle": "approved"},
                {"id": "e3", "source": "gate", "target": "stop", "sourceHandle": "rejected"},
            ],
        },
    },
    {
        "id": "ticket-triage-classify",
        "title": "Ticket triage (LLM classify)",
        "description": (
            "Phase 35 (fase 4.1) — classify an incoming message into a fixed set of "
            "categories with guaranteed structure, route on the result with a switch, "
            "and log every triaged ticket to a CSV in the workspace storage. Run it "
            "with a payload like {\"text\": \"my invoice is wrong\"}."
        ),
        "category": "ai",
        "node_types": ["manual", "llm.classify", "switch", "file.write", "notify.inapp"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Ticket in",
                 "position": {"x": 40, "y": 80}},
                {"id": "triage", "type": "llm.classify", "name": "Classify",
                 "params": {
                     "input": "={{ default($trigger.text, 'I cannot log in to my account') }}",
                     "categories": ["billing", "bug", "question"],
                 },
                 "position": {"x": 280, "y": 80}},
                {"id": "route", "type": "switch", "name": "Route",
                 "params": {"value": "={{ $node.triage.output.category }}",
                            "cases": ["billing", "bug"]},
                 "position": {"x": 520, "y": 80}},
                {"id": "billing", "type": "notify.inapp", "name": "Billing queue",
                 "params": {"title": "Billing ticket", "body": "={{ $trigger.text }}"},
                 "position": {"x": 760, "y": 10}},
                {"id": "bug", "type": "notify.inapp", "name": "Bug queue",
                 "params": {"title": "Bug ticket", "body": "={{ $trigger.text }}"},
                 "position": {"x": 760, "y": 110}},
                {"id": "other", "type": "notify.inapp", "name": "General queue",
                 "params": {"title": "Question", "body": "={{ $trigger.text }}"},
                 "position": {"x": 760, "y": 210}},
                {"id": "log", "type": "file.write", "name": "Append to CSV log",
                 "params": {
                     "path": "tickets/triage-log.csv",
                     "format": "csv",
                     "append": "true",
                     "content": "={{ [{'category': $node.triage.output.category, 'text': default($trigger.text, '')}] }}",
                 },
                 "position": {"x": 520, "y": 230}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "triage"},
                {"id": "e2", "source": "triage", "target": "route"},
                {"id": "e3", "source": "route", "target": "billing", "sourceHandle": "case:billing"},
                {"id": "e4", "source": "route", "target": "bug", "sourceHandle": "case:bug"},
                {"id": "e5", "source": "route", "target": "other", "sourceHandle": "default"},
                {"id": "e6", "source": "triage", "target": "log"},
            ],
        },
    },
    {
        "id": "kb-search-rag",
        "title": "Knowledge-base RAG answer",
        "description": (
            "Phase 38 (fase 6.5) — retrieval-augmented answering with the dedicated "
            "kb.search node (not a generic agent): search the knowledge base for a "
            "question, then have an LLM answer strictly from the retrieved passages. "
            "Run it with a payload like {\"question\": \"how do I configure SMTP?\"}."
        ),
        "category": "ai",
        "node_types": ["manual", "kb.search", "llm.completion", "set"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Ask",
                 "position": {"x": 40, "y": 80}},
                {"id": "kb", "type": "kb.search", "name": "KB search",
                 "params": {
                     "query": "={{ default($trigger.question, 'How do I configure SMTP?') }}",
                     "top_k": 5,
                 },
                 "position": {"x": 280, "y": 80}},
                {"id": "answer", "type": "llm.completion", "name": "Answer from context",
                 "params": {
                     "system": "Answer only from the provided passages. If they don't contain the answer, say you don't know.",
                     "prompt": "Question: ={{ default($trigger.question, 'How do I configure SMTP?') }}\n\nPassages:\n={{ $node.kb.output.results }}",
                 },
                 "position": {"x": 540, "y": 80}},
                {"id": "out", "type": "set", "name": "Response",
                 "params": {"fields": {
                     "answer": "={{ $node.answer.output.content }}",
                     "sources": "={{ $node.kb.output.count }}",
                 }},
                 "position": {"x": 800, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "kb"},
                {"id": "e2", "source": "kb", "target": "answer"},
                {"id": "e3", "source": "answer", "target": "out"},
            ],
        },
    },
    {
        "id": "paginate-while",
        "title": "Cursor pagination (while loop)",
        "description": (
            "Phase 38 (fase 6.3) — a condition-driven while loop for pagination / "
            "async-API polling without subworkflow recursion. The condition is "
            "re-evaluated before each pass ($item = previous body output, $index = "
            "iteration number) under a mandatory cap; the body results are collected on "
            "'done' as {items, count, capped}."
        ),
        "category": "logic",
        "node_types": ["manual", "while", "http.request", "set"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Run",
                 "position": {"x": 40, "y": 120}},
                {"id": "pager", "type": "while", "name": "While more pages",
                 "params": {
                     "condition": "={{ $index == 0 or default($item.has_more, false) }}",
                     "maxIterations": 5,
                 },
                 "position": {"x": 280, "y": 120}},
                {"id": "page", "type": "http.request", "name": "Body: fetch page",
                 "params": {"method": "GET", "url": "https://example.com", "timeout": 10,
                            "allow_errors": "true"},
                 "position": {"x": 280, "y": 270}},
                {"id": "norm", "type": "set", "name": "Body: normalize + advance cursor",
                 "params": {"fields": {
                     "page": "={{ $index }}",
                     "status": "={{ $node.page.output.status }}",
                     "has_more": "={{ $index < 2 }}",
                 }},
                 "position": {"x": 540, "y": 270}},
                {"id": "collect", "type": "set", "name": "Collected pages",
                 "params": {"fields": {
                     "pages": "={{ $node.pager.output.items }}",
                     "count": "={{ $node.pager.output.count }}",
                 }},
                 "position": {"x": 540, "y": 120}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "pager"},
                {"id": "e2", "source": "pager", "target": "page", "sourceHandle": "loop"},
                {"id": "e3", "source": "page", "target": "norm"},
                {"id": "e4", "source": "pager", "target": "collect", "sourceHandle": "done"},
            ],
        },
    },
    {
        "id": "extract-to-db",
        "title": "Extract fields → SQL insert",
        "description": (
            "Phase 35 (fase 4.1 + 4.2) — pull typed fields from free text with "
            "llm.extract (guaranteed to match a JSON Schema), then persist them with a "
            "parameterised db.query INSERT into a sqlite database in workspace storage. "
            "Run it with a payload like {\"text\": \"Invoice #42 for ACME, 1500 EUR, due 2026-08-01\"}."
        ),
        "category": "data",
        "node_types": ["manual", "db.query", "llm.extract", "set"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Text in",
                 "position": {"x": 40, "y": 80}},
                {"id": "create", "type": "db.query", "name": "Ensure table",
                 "params": {
                     "driver": "sqlite",
                     "database": "invoices.db",
                     "query": "CREATE TABLE IF NOT EXISTS invoices (name TEXT, amount REAL, due TEXT)",
                     "params": [],
                 },
                 "position": {"x": 260, "y": 80}},
                {"id": "parse", "type": "llm.extract", "name": "Extract fields",
                 "params": {
                     "input": "={{ default($trigger.text, 'Invoice #42 for ACME, amount 1500 EUR, due 2026-08-01') }}",
                     "schema": {
                         "type": "object",
                         "required": ["name", "amount"],
                         "properties": {
                             "name": {"type": "string"},
                             "amount": {"type": "number"},
                             "due": {"type": "string"},
                         },
                     },
                 },
                 "position": {"x": 500, "y": 80}},
                {"id": "save", "type": "db.query", "name": "Insert row",
                 "params": {
                     "driver": "sqlite",
                     "database": "invoices.db",
                     "query": "INSERT INTO invoices (name, amount, due) VALUES (?, ?, ?)",
                     "params": "={{ [$node.parse.output.data.name, $node.parse.output.data.amount, default($node.parse.output.data.due, '')] }}",
                 },
                 "position": {"x": 760, "y": 80}},
                {"id": "out", "type": "set", "name": "Result",
                 "params": {"fields": {
                     "extracted": "={{ $node.parse.output.data }}",
                     "inserted": "={{ $node.save.output.rowcount }}",
                 }},
                 "position": {"x": 1020, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "create"},
                {"id": "e2", "source": "create", "target": "parse"},
                {"id": "e3", "source": "parse", "target": "save"},
                {"id": "e4", "source": "save", "target": "out"},
            ],
        },
    },
    {
        "id": "file-read-report",
        "title": "Write then read a CSV file",
        "description": (
            "Phase 35 (fase 4.2) — self-contained file I/O in workspace storage: build "
            "a list of rows, write them as a CSV with file.write, read them back with "
            "file.read (parsed to {rows, count}), then summarise. Every path is sandboxed "
            "inside GRAPH_WORKFLOW_FILES_DIR."
        ),
        "category": "data",
        "node_types": ["manual", "set", "file.write", "file.read"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Run",
                 "position": {"x": 40, "y": 80}},
                {"id": "make", "type": "set", "name": "Rows",
                 "params": {"fields": {
                     "rows": "={{ [{'city': 'Milano', 'temp': 30}, {'city': 'Roma', 'temp': 32}] }}",
                 }},
                 "position": {"x": 260, "y": 80}},
                {"id": "write", "type": "file.write", "name": "Write CSV",
                 "params": {
                     "path": "reports/cities.csv",
                     "format": "csv",
                     "append": "false",
                     "content": "={{ $node.make.output.rows }}",
                 },
                 "position": {"x": 500, "y": 80}},
                {"id": "read", "type": "file.read", "name": "Read CSV back",
                 "params": {"path": "reports/cities.csv", "format": "csv"},
                 "position": {"x": 740, "y": 80}},
                {"id": "out", "type": "set", "name": "Summary",
                 "params": {"fields": {
                     "count": "={{ $node.read.output.count }}",
                     "rows": "={{ $node.read.output.rows }}",
                 }},
                 "position": {"x": 980, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "make"},
                {"id": "e2", "source": "make", "target": "write"},
                {"id": "e3", "source": "write", "target": "read"},
                {"id": "e4", "source": "read", "target": "out"},
            ],
        },
    },
    {
        "id": "parse-http-json",
        "title": "Parse an HTTP JSON body in transit",
        "description": (
            "Phase 35 (fase 4.2) — parse a textual payload without touching disk: fetch "
            "an endpoint with http.request, then hand its raw text to file.parse "
            "(format json → {data}). Same outputs as file.read, but for in-transit data "
            "such as an API body or a tool result."
        ),
        "category": "data",
        "node_types": ["manual", "http.request", "file.parse", "set"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Run",
                 "position": {"x": 40, "y": 80}},
                {"id": "fetch", "type": "http.request", "name": "Fetch JSON",
                 "params": {"method": "GET", "url": "https://httpbin.org/json", "timeout": 15,
                            "allow_errors": "true"},
                 "position": {"x": 280, "y": 80}},
                {"id": "parse", "type": "file.parse", "name": "Parse body",
                 "params": {"content": "={{ $node.fetch.output.text }}", "format": "json"},
                 "position": {"x": 540, "y": 80}},
                {"id": "out", "type": "set", "name": "Result",
                 "params": {"fields": {"data": "={{ $node.parse.output.data }}"}},
                 "position": {"x": 800, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "fetch"},
                {"id": "e2", "source": "fetch", "target": "parse"},
                {"id": "e3", "source": "parse", "target": "out"},
            ],
        },
    },
    {
        "id": "chatbot-reply",
        "title": "Chatbot (chat trigger → reply)",
        "description": (
            "Phase 41 (fase 9.3) — turn a workflow into a chatbot: a chat trigger feeds "
            "an LLM and a chat.reply node returns the answer. Call "
            "POST /v1/graph-workflows/{id}/chat with {message, session_id?}; the graph "
            "sees $trigger {session_id, message, history} and session state persists "
            "across turns."
        ),
        "category": "ai",
        "node_types": ["chat", "llm.completion", "chat.reply"],
        "graph": {
            "nodes": [
                {"id": "in", "type": "chat", "name": "On chat message",
                 "position": {"x": 40, "y": 80}},
                {"id": "llm", "type": "llm.completion", "name": "Generate reply",
                 "params": {
                     "system": "You are a helpful, concise assistant.",
                     "prompt": "Conversation so far:\n={{ $trigger.history }}\n\nUser: ={{ $trigger.message }}\nAssistant:",
                 },
                 "position": {"x": 300, "y": 80}},
                {"id": "reply", "type": "chat.reply", "name": "Reply",
                 "params": {"text": "={{ $node.llm.output.content }}"},
                 "position": {"x": 560, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "in", "target": "llm"},
                {"id": "e2", "source": "llm", "target": "reply"},
            ],
        },
    },
    {
        "id": "success-pipeline",
        "title": "Run on another workflow's success",
        "description": (
            "Phase 38 (fase 6.1) — the mirror of the error trigger: a success trigger "
            "fires when another workflow's run completes successfully (empty "
            "config.workflow_id = watch every workflow), landing $trigger "
            "{workflow_id, workflow_name, run_id, output}. Chains 'A then B' pipelines "
            "without a subworkflow node."
        ),
        "category": "automation",
        "node_types": ["success", "set", "notify.inapp"],
        "graph": {
            "nodes": [
                {"id": "ok", "type": "success", "name": "On workflow success",
                 "position": {"x": 40, "y": 80}},
                {"id": "msg", "type": "set", "name": "Format",
                 "params": {"fields": {
                     "title": "Completed: ={{ default($trigger.workflow_name, $trigger.workflow_id) }}",
                     "text": "Run ={{ $trigger.run_id }} output: ={{ $trigger.output }}",
                 }},
                 "position": {"x": 280, "y": 80}},
                {"id": "bell", "type": "notify.inapp", "name": "Notify",
                 "params": {"title": "={{ $node.msg.output.title }}", "body": "={{ $node.msg.output.text }}"},
                 "position": {"x": 540, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "ok", "target": "msg"},
                {"id": "e2", "source": "msg", "target": "bell"},
            ],
        },
    },
    {
        "id": "expense-approval-form",
        "title": "Expense approval form",
        "description": (
            "Phase 42 (fase 10.1) — the run suspends on a human.input node until "
            "someone fills a JSON-Schema form (amount + category) from Workflow → "
            "Runs (or POST /approvals/{id}/submit); the validated data resumes the "
            "run as {data}. A timeout routes through its own branch. Run it with a "
            "payload like {\"requester\": \"jane\"}."
        ),
        "category": "ops",
        "node_types": ["manual", "human.input", "notify.inapp"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Request",
                 "position": {"x": 40, "y": 80}},
                {"id": "form", "type": "human.input", "name": "Expense details",
                 "params": {
                     "title": "Expense approval",
                     "message": "={{ default($trigger.requester, 'Someone') }} needs an amount + category.",
                     "schema": {
                         "type": "object",
                         "required": ["amount", "category"],
                         "properties": {
                             "amount": {"type": "number"},
                             "category": {"type": "string", "enum": ["travel", "meals", "software", "other"]},
                         },
                     },
                     "timeout": 86400,
                     "onTimeout": "branch",
                 },
                 "position": {"x": 280, "y": 80}},
                {"id": "logged", "type": "notify.inapp", "name": "Logged",
                 "params": {"title": "Expense logged",
                            "body": "={{ $node.form.output.data.category }}: ={{ $node.form.output.data.amount }}"},
                 "position": {"x": 540, "y": 30}},
                {"id": "expired", "type": "notify.inapp", "name": "Timed out",
                 "params": {"title": "Expense form timed out", "body": "Nobody filled the form in time."},
                 "position": {"x": 540, "y": 160}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "form"},
                {"id": "e2", "source": "form", "target": "logged", "sourceHandle": "submitted"},
                {"id": "e3", "source": "form", "target": "expired", "sourceHandle": "timeout"},
            ],
        },
    },
    {
        "id": "payment-webhook-wait",
        "title": "Wait for payment confirmation",
        "description": (
            "Phase 42 (fase 10.2) — the run suspends on a wait.event node until an "
            "external system (e.g. a payment provider) POSTs to /graph-workflows/"
            "events/{order_id}; the delivered payload resumes the run as its output. "
            "Covers real async callbacks (payments, signatures, tickets) without "
            "polling. Run it with a payload like {\"order_id\": \"ord-123\"}."
        ),
        "category": "automation",
        "node_types": ["manual", "wait.event", "notify.inapp"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Checkout",
                 "position": {"x": 40, "y": 80}},
                {"id": "wait", "type": "wait.event", "name": "Wait for payment",
                 "params": {"correlationId": "={{ $trigger.order_id }}", "timeout": 3600, "onTimeout": "branch"},
                 "position": {"x": 280, "y": 80}},
                {"id": "paid", "type": "notify.inapp", "name": "Payment received",
                 "params": {"title": "Order ={{ $trigger.order_id }} paid",
                            "body": "Payload: ={{ $node.wait.output }}"},
                 "position": {"x": 540, "y": 30}},
                {"id": "expired", "type": "notify.inapp", "name": "Payment timed out",
                 "params": {"title": "Order ={{ $trigger.order_id }} unpaid",
                            "body": "No confirmation arrived within the deadline."},
                 "position": {"x": 540, "y": 160}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "wait"},
                {"id": "e2", "source": "wait", "target": "paid", "sourceHandle": "main"},
                {"id": "e3", "source": "wait", "target": "expired", "sourceHandle": "timeout"},
            ],
        },
    },
    {
        "id": "http-mock-pin-demo",
        "title": "Mocked HTTP integration (test-ready)",
        "description": (
            "Phase 43 (roadmap fase 11) — the http.request node ships with a "
            "**pinned output**, so 'Run tests' and 'Dry-run' in the run panel work "
            "immediately without ever hitting the real API. Point 'url' at your real "
            "endpoint and clear the pin (or leave it — pins never affect a normal "
            "Run now) once you are ready to go live."
        ),
        "category": "automation",
        "node_types": ["manual", "http.request", "set"],
        "graph": {
            "nodes": [
                {"id": "start", "type": "manual", "name": "Start",
                 "position": {"x": 40, "y": 80}},
                {"id": "call", "type": "http.request", "name": "Fetch user",
                 "params": {"url": "https://api.example.com/users/={{ $trigger.user_id }}", "method": "GET"},
                 "pinnedOutput": {"status": 200, "body": {"id": 42, "name": "Ada Lovelace"}},
                 "position": {"x": 280, "y": 80}},
                {"id": "profile", "type": "set", "name": "Profile",
                 "params": {"fields": {
                     "id": "={{ $node.call.output.body.id }}",
                     "name": "={{ $node.call.output.body.name }}",
                 }},
                 "position": {"x": 540, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "call"},
                {"id": "e2", "source": "call", "target": "profile"},
            ],
        },
    },
    {
        "id": "idempotent-order-saga",
        "title": "Idempotent order processing with rollback",
        "description": (
            "Phase 48 (roadmap fase 16) — a webhook that processes an order exactly "
            "once and compensates on failure. Add a `dedupKey` of `{{ $trigger.order_id }}` "
            "on the webhook trigger so a retried delivery returns the original run. "
            "`state.increment` keeps a persistent processed-count across runs; the "
            "'reserve' step wires a `compensate` edge to 'release', which the engine "
            "runs in reverse order if a later step fails."
        ),
        "category": "automation",
        "node_types": ["webhook", "state.increment", "state.set", "http.request", "set"],
        "graph": {
            "nodes": [
                {"id": "hook", "type": "webhook", "name": "Order received (POST)",
                 "position": {"x": 40, "y": 120}},
                {"id": "count", "type": "state.increment", "name": "Processed count",
                 "params": {"key": "orders_processed"},
                 "position": {"x": 260, "y": 120}},
                {"id": "reserve", "type": "state.set", "name": "Reserve stock",
                 "params": {"key": "reserved:={{ $trigger.order_id }}", "value": True},
                 "position": {"x": 480, "y": 120}},
                {"id": "charge", "type": "http.request", "name": "Charge payment",
                 "params": {"url": "https://api.example.com/charge", "method": "POST",
                            "body": {"order": "={{ $trigger.order_id }}"}},
                 "pinnedOutput": {"status": 200, "body": {"charged": True}},
                 "position": {"x": 700, "y": 120}},
                {"id": "done", "type": "set", "name": "Confirm",
                 "params": {"fields": {"ok": True, "order": "={{ $trigger.order_id }}"}},
                 "position": {"x": 920, "y": 120}},
                {"id": "release", "type": "state.set", "name": "Release stock (compensate)",
                 "params": {"key": "reserved:={{ $trigger.order_id }}", "value": False},
                 "position": {"x": 480, "y": 300}},
            ],
            "edges": [
                {"id": "e1", "source": "hook", "target": "count"},
                {"id": "e2", "source": "count", "target": "reserve"},
                {"id": "e3", "source": "reserve", "target": "charge"},
                {"id": "e4", "source": "charge", "target": "done"},
                {"id": "e5", "source": "reserve", "target": "release", "sourceHandle": "compensate"},
            ],
        },
    },
    {
        "id": "nightly-report-blackout",
        "title": "Nightly report with blackout & digest (Phase 49)",
        "description": (
            "A scheduled nightly report that respects a deploy blackout window and "
            "SLA. Give the `schedule` trigger a `tz` (e.g. \"Europe/Rome\") and a "
            "nightly `recurrence`; then on the workflow set "
            "`blackout={\"windows\":[{\"start\":\"01:00\",\"end\":\"02:30\"}],"
            "\"on_conflict\":\"defer\"}` so a run due during the nightly deploy waits "
            "until the window clears, `sla={\"max_duration_s\":120,"
            "\"missed_grace_s\":900,\"channels\":[\"inapp\"]}` to be alerted if a run "
            "overruns or a beat is missed, and "
            "`notify={\"digest\":{\"enabled\":true,\"interval_s\":86400,"
            "\"channel\":\"inapp\"}}` to receive one daily summary instead of a "
            "message per run. Configure these via PATCH /v1/graph-workflows/{id}."
        ),
        "category": "ops",
        "node_types": ["schedule", "set", "notify.inapp"],
        "graph": {
            "nodes": [
                {"id": "trigger", "type": "schedule", "name": "Nightly (Europe/Rome)",
                 "params": {"recurrence": "daily", "tz": "Europe/Rome"},
                 "position": {"x": 40, "y": 80}},
                {"id": "report", "type": "set", "name": "Compose report",
                 "params": {"fields": {
                     "title": "Nightly report",
                     "generated_at": "={{ now }}",
                     "body": "All systems nominal.",
                 }},
                 "position": {"x": 300, "y": 80}},
                {"id": "notify", "type": "notify.inapp", "name": "Notify",
                 "params": {
                     "title": "={{ $node.report.output.title }}",
                     "body": "={{ $node.report.output.body }}",
                 },
                 "position": {"x": 560, "y": 80}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger", "target": "report"},
                {"id": "e2", "source": "report", "target": "notify"},
            ],
        },
    },
]


def list_graph_workflow_examples() -> list[dict]:
    """The curated graph-workflow examples (read-only)."""
    return GRAPH_WORKFLOW_EXAMPLES
