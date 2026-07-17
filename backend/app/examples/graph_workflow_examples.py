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
]


def list_graph_workflow_examples() -> list[dict]:
    """The curated graph-workflow examples (read-only)."""
    return GRAPH_WORKFLOW_EXAMPLES
