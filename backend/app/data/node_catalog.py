"""
Phase 29 — node-type catalog for the visual editor palette.

Static, read-only metadata describing every node kind the engine can execute:
its category, human label, i/o shape and a light params schema used to render
the inspector. ``tool.*`` action nodes are generated from the live tool
registry so any built-in / MCP / custom tool becomes a node with zero new code.
"""

from app.schemas.graph_workflows import NodeTypeInfo


def _param(name: str, label: str, kind: str = "text", **extra) -> dict:
    return {"name": name, "label": label, "kind": kind, **extra}


def _ab_params() -> list[dict]:
    """Fase 18.2 — prompt A/B testing fields shared by the ``llm.*`` nodes. When
    ``variants`` is a non-empty list, each run picks one variant (its params
    overlay the node's) and the choice is recorded on the output for the per-node
    metrics variant breakdown (GET /{id}/nodes/{node}/variants)."""
    return [
        _param(
            "variants", "A/B variants (JSON array)", "json",
            hint='e.g. [{"name":"concise","params":{"prompt":"..."}},{"name":"detailed","weight":2,"params":{"model":"..."}}]',
        ),
        _param(
            "variantStrategy", "Variant strategy", "select",
            options=["round-robin", "weighted"],
            hint="round-robin alternates evenly across runs; weighted samples by each variant's weight",
        ),
    ]


_STATIC_NODES: list[NodeTypeInfo] = [
    # ── triggers ──
    NodeTypeInfo(
        type="manual", category="trigger", label="Manual", inputs=0, outputs=["main"],
        description="Starts the workflow on demand (Run now) with an optional payload.",
        params_schema=[_param("note", "Note", "text")],
    ),
    NodeTypeInfo(
        type="schedule", category="trigger", label="Schedule", inputs=0, outputs=["main"],
        description="Fires on a cron / RRULE / natural-language schedule.",
        params_schema=[_param("note", "Note", "text")],
    ),
    NodeTypeInfo(
        type="webhook", category="trigger", label="Webhook", inputs=0, outputs=["main"],
        description="Fires when its public token URL receives a POST; the body becomes $trigger.",
        params_schema=[_param("note", "Note", "text")],
    ),
    NodeTypeInfo(
        type="event", category="trigger", label="Event", inputs=0, outputs=["main"],
        description="Fires on an internal event (document ingested, reminder fired, run completed).",
        params_schema=[_param("note", "Note", "text")],
    ),
    NodeTypeInfo(
        type="error", category="trigger", label="On workflow error", inputs=0, outputs=["main"],
        description=(
            "Fires when another workflow's run fails (attach an 'error' trigger to "
            "choose which workflow to watch, or watch them all). $trigger carries "
            "{workflow_id, workflow_name, run_id, error, failed_node} — ideal for "
            "centralised alerting via the notify nodes."
        ),
        params_schema=[_param("note", "Note", "text")],
    ),
    # ── triggers (Phase 38 — roadmap fase 6.1/6.2) ──
    NodeTypeInfo(
        type="success", category="trigger", label="On workflow success", inputs=0, outputs=["main"],
        description=(
            "Fires when another workflow's run completes successfully (attach a "
            "'success' trigger to choose which workflow to watch, or watch them "
            "all). $trigger carries {workflow_id, workflow_name, run_id, output} — "
            "enables 'A then B' pipelines without subworkflows."
        ),
        params_schema=[_param("note", "Note", "text")],
    ),
    NodeTypeInfo(
        type="file.watch", category="trigger", label="File watch", inputs=0, outputs=["main"],
        description=(
            "Fires when a file is created or modified in a subfolder of the "
            "workspace storage (poll-based; attach a 'file.watch' trigger with "
            "{path, pattern, events, interval}). $trigger = {path, event, size}."
        ),
        params_schema=[_param("note", "Note", "text")],
    ),
    NodeTypeInfo(
        type="email.inbound", category="trigger", label="Inbound email", inputs=0, outputs=["main"],
        description=(
            "Fires on new IMAP messages (poll-based; attach an 'email.inbound' "
            "trigger with {host, username, password_secret, from, subject}). "
            "$trigger = {from, subject, body, attachments} — attachments are saved "
            "to the workspace storage, readable with File Read."
        ),
        params_schema=[_param("note", "Note", "text")],
    ),
    # ── trigger (Phase 46 — roadmap fase 14.4) ──
    NodeTypeInfo(
        type="queue.consume", category="trigger", label="Queue consume", inputs=0, outputs=["main"],
        description=(
            "Fires once per message consumed off a message-queue topic "
            "(poll-based, like File watch; attach a 'queue.consume' trigger "
            "with {topic, batch_size}). Backed by the pluggable QueueDriver "
            "(GRAPH_WORKFLOW_QUEUE_DRIVER=db|memory today; a real broker — "
            "AMQP/Kafka/MQTT — plugs in as another driver). $trigger = "
            "{message, topic, headers}."
        ),
        params_schema=[_param("note", "Note", "text")],
    ),
    # ── trigger (Phase 47 — roadmap fase 15.4) ──
    NodeTypeInfo(
        type="rss.read", category="trigger", label="RSS/Atom feed", inputs=0, outputs=["main"],
        description=(
            "Fires once per new entry of an RSS/Atom feed (poll-based, deduped by "
            "guid; attach an 'rss.read' trigger with {url, interval}). "
            "$trigger = {title, link, published, summary, guid}. The first poll "
            "only seeds the seen-set so a backlog doesn't storm the engine."
        ),
        params_schema=[_param("note", "Note", "text")],
    ),
    # ── trigger (Phase 41 — roadmap fase 9.3) ──
    NodeTypeInfo(
        type="chat", category="trigger", label="Chat message", inputs=0, outputs=["main"],
        description=(
            "Turns the workflow into a chatbot: fires once per conversation "
            "message received on POST /{id}/chat. $trigger = {session_id, "
            "message, history} — session state persists across turns. End the "
            "graph with a 'Chat reply' node to return the answer to the caller."
        ),
        params_schema=[_param("note", "Note", "text")],
    ),
    # ── logic ──
    NodeTypeInfo(
        type="if", category="logic", label="If", outputs=["true", "false"],
        description="Routes to the true/false branch based on a boolean expression.",
        params_schema=[_param("condition", "Condition (expression)", "expression")],
    ),
    NodeTypeInfo(
        type="switch", category="logic", label="Switch", outputs=["case:a", "case:b", "default"],
        description="Routes to the branch whose case matches the resolved value.",
        params_schema=[
            _param("value", "Value (expression)", "expression"),
            _param("cases", "Cases (JSON array)", "json"),
        ],
    ),
    NodeTypeInfo(
        type="merge", category="logic", label="Merge", inputs=2, outputs=["main"],
        description="Collects every live incoming input into a single list.",
    ),
    NodeTypeInfo(
        type="for", category="logic", label="For-each", outputs=["loop", "done"],
        description=(
            "Runs the body wired to its 'loop' output once per array item ($item / "
            "$index available), collects the results, then continues on 'done' "
            "(output {items, count})."
        ),
        params_schema=[_param("items", "Items (expression → array)", "expression")],
    ),
    NodeTypeInfo(
        type="repeat", category="logic", label="Repeat", outputs=["loop", "done"],
        description=(
            "Runs the body wired to its 'loop' output N times ($index available), "
            "collects the results, then continues on 'done' (output {items, count})."
        ),
        params_schema=[_param("times", "Times", "number")],
    ),
    NodeTypeInfo(
        type="while", category="logic", label="While", outputs=["loop", "done"],
        description=(
            "Runs the body wired to its 'loop' output while the condition stays "
            "truthy, under a mandatory iteration cap. $item is the previous "
            "iteration's body output (the node input on the first pass), $index "
            "the iteration number. Continues on 'done' with {items, count, capped} "
            "— covers async-API polling and pagination without subworkflow recursion."
        ),
        params_schema=[
            _param("condition", "Condition (expression, re-evaluated per iteration)", "expression"),
            _param("maxIterations", "Max iterations (default 100)", "number"),
        ],
    ),
    NodeTypeInfo(
        type="wait", category="logic", label="Wait", outputs=["main"],
        description=(
            "Suspends the node for a fixed duration or until a point in time, then "
            "continues. Output: {waited} (seconds actually slept, capped at 1h)."
        ),
        params_schema=[
            _param("seconds", "Seconds", "number"),
            _param("until", "Until (unix timestamp or ISO datetime, expression)", "expression"),
        ],
    ),
    # ── data ──
    NodeTypeInfo(
        type="set", category="data", label="Set", outputs=["main"],
        description="Builds an output object from fixed values and expressions.",
        params_schema=[_param("fields", "Fields (JSON object of expressions)", "json")],
    ),
    NodeTypeInfo(
        type="filter", category="data", label="Filter", outputs=["main"],
        description="Keeps the array items whose per-item condition is truthy.",
        params_schema=[
            _param("items", "Items (expression)", "expression"),
            _param("keep", "Keep mask (expression → bool[])", "expression"),
        ],
    ),
    NodeTypeInfo(
        type="code", category="data", label="Code", outputs=["main"],
        description="Runs Python in the sandbox with `input` in scope; prints become the output.",
        params_schema=[_param("code", "Python code", "code")],
    ),
    NodeTypeInfo(
        type="aggregate", category="data", label="Aggregate", outputs=["main"],
        description=(
            "Reduces an array (items, or the node input) with 'op' "
            "(sum/avg/min/max/count/concat) over a dotted 'field' path. "
            "Output: {result, count}."
        ),
        params_schema=[
            _param("items", "Items (expression, optional)", "expression"),
            _param("op", "Operation (sum/avg/min/max/count/concat)", "text"),
            _param("field", "Field (dotted path)", "text"),
        ],
    ),
    NodeTypeInfo(
        type="batch", category="data", label="Batch", outputs=["main"],
        description=(
            "Splits an array (items, or the node input) into chunks of 'size', "
            "typically fed into a For-each. Output: {batches, count}."
        ),
        params_schema=[
            _param("items", "Items (expression, optional)", "expression"),
            _param("size", "Batch size", "number"),
        ],
    ),
    # ── data: db / file nodes (Phase 35 — roadmap fase 4.2) ──
    NodeTypeInfo(
        type="db.query", category="data", label="DB Query", outputs=["main"],
        description=(
            "Runs a parameterised SQL query. sqlite databases live inside the workspace "
            "storage (GRAPH_WORKFLOW_FILES_DIR); postgres connects via a DSN — keep it in "
            "$secrets. Output: {rows, count, rowcount} (max 1000 rows)."
        ),
        params_schema=[
            _param("driver", "Driver", "select", options=["sqlite", "postgres"]),
            _param("database", "Database (sqlite path in workspace storage)", "text"),
            _param("dsn", "DSN (postgres, expression)", "expression",
                   hint="e.g. ={{ $secrets.PG_DSN }}"),
            _param("query", "SQL query (use ? / $1 placeholders)", "code"),
            _param("params", "Parameters (JSON array)", "json"),
        ],
    ),
    NodeTypeInfo(
        type="file.read", category="data", label="File Read", outputs=["main"],
        description=(
            "Reads a file from the workspace storage and parses it. Output by format: "
            "json → {data}, csv → {rows, count}, lines → {lines, count}, text → {text, size}."
        ),
        params_schema=[
            _param("path", "Path (inside workspace storage)", "text"),
            _param("format", "Format", "select", options=["auto", "text", "json", "csv", "lines"]),
            _param("delimiter", "CSV delimiter", "text"),
        ],
    ),
    NodeTypeInfo(
        type="file.write", category="data", label="File Write", outputs=["main"],
        description=(
            "Writes content (or this node's input) to a file in the workspace storage. "
            "Objects/arrays serialise as JSON; format csv renders rows of objects. "
            "Output: {path, format, bytes_written, append}."
        ),
        params_schema=[
            _param("path", "Path (inside workspace storage)", "text"),
            _param("content", "Content (expression, defaults to input)", "expression"),
            _param("format", "Format", "select", options=["auto", "text", "json", "csv"]),
            _param("append", "Append (true/false)", "text"),
        ],
    ),
    NodeTypeInfo(
        type="file.parse", category="data", label="Parse", outputs=["main"],
        description=(
            "Parses an in-flight text payload (http body, tool result…) without touching "
            "disk: json → {data}, csv → {rows, count}, lines → {lines, count}."
        ),
        params_schema=[
            _param("content", "Content (expression, defaults to input)", "expression"),
            _param("format", "Format", "select", options=["auto", "json", "csv", "lines"]),
            _param("delimiter", "CSV delimiter", "text"),
        ],
    ),
    # ── action ──
    NodeTypeInfo(
        type="http.request", category="action", label="HTTP Request", outputs=["main"],
        description=(
            "Calls an external HTTP API. Non-2xx responses raise (so retry / On error "
            "apply) unless 'Allow non-2xx' is set. Output: {status, ok, headers, json, text}."
        ),
        params_schema=[
            _param("method", "Method (GET/POST/…)", "text"),
            _param("url", "URL (expression)", "expression"),
            _param("query", "Query params (JSON object)", "json"),
            _param("headers", "Headers (JSON object)", "json"),
            _param("body", "Body (JSON or raw text)", "json"),
            _param("timeout", "Timeout (seconds, max 120)", "number"),
            _param("allow_errors", "Allow non-2xx (true/false)", "text"),
            _param(
                "maxRequestsPerMinute", "Max requests/minute to this host", "number",
                hint="Fase 6.6 — over the cap the call waits (never fails); combines with GRAPH_WORKFLOW_RATE_LIMITS",
            ),
        ],
        # Fase 2.1 — sensible retry preset applied by the editor on drop:
        # transient HTTP failures retry twice with exponential backoff.
        defaults={"retry": 2, "backoff": 2, "backoffStrategy": "exponential", "timeoutMs": 60000},
    ),
    NodeTypeInfo(
        type="queue.publish", category="action", label="Queue publish", outputs=["main"],
        description=(
            "Publishes a message to a message-queue topic via the active "
            "QueueDriver (GRAPH_WORKFLOW_QUEUE_DRIVER=db|memory), typically "
            "consumed by another workflow's 'Queue consume' trigger. Output: "
            "{topic, published}."
        ),
        params_schema=[
            _param("topic", "Topic", "text"),
            _param("message", "Message (expression, defaults to input)", "expression"),
            _param("headers", "Headers (JSON object)", "json"),
        ],
    ),
    NodeTypeInfo(
        type="subworkflow", category="action", label="Subworkflow", outputs=["main"],
        description=(
            "Runs another workflow inline as a child run and returns its sink output(s). "
            "The payload (or this node's input) becomes the child's $trigger. Max nesting: 5."
        ),
        params_schema=[
            _param("workflow_id", "Workflow", "workflow"),
            _param("payload", "Payload (JSON object → child $trigger)", "json"),
        ],
    ),
    NodeTypeInfo(
        type="human.approval", category="action", label="Human approval",
        outputs=["approved", "rejected"],
        description=(
            "Suspends the run (status 'waiting') until someone approves or rejects the "
            "request from Workflow → Runs (or POST /approvals/{id}/decision), then routes "
            "through the matching branch. Sends an in-app notification (and optionally "
            "Telegram). A timeout follows 'On timeout' (reject | fail). Survives restarts."
        ),
        params_schema=[
            _param("title", "Title", "text"),
            _param("message", "Message (expression)", "expression"),
            _param("timeout", "Timeout (seconds, default 86400, max 7 days)", "number"),
            _param("onTimeout", "On timeout", "select", options=["reject", "fail"]),
            _param("telegram", "Also notify via Telegram (true/false)", "text"),
        ],
    ),
    # ── action: advanced human-in-the-loop (Phase 42 — roadmap fase 10) ──
    NodeTypeInfo(
        type="human.input", category="action", label="Human input",
        outputs=["submitted", "timeout"],
        description=(
            "Suspends the run (status 'waiting') until someone fills a form defined by "
            "a JSON Schema, from Workflow → Runs (or POST /approvals/{id}/submit), then "
            "resumes with the validated data as {data}. Sends an in-app notification "
            "(and optionally Telegram). 'On timeout' routes to the 'timeout' branch, or "
            "fails the node. Survives restarts."
        ),
        params_schema=[
            _param("title", "Title", "text"),
            _param("message", "Message (expression)", "expression"),
            _param("schema", "Form JSON Schema", "json"),
            _param("timeout", "Timeout (seconds, default 86400, max 7 days)", "number"),
            _param("onTimeout", "On timeout", "select", options=["branch", "fail"]),
            _param("telegram", "Also notify via Telegram (true/false)", "text"),
        ],
    ),
    NodeTypeInfo(
        type="wait.event", category="action", label="Wait for event",
        outputs=["main", "timeout"],
        description=(
            "Suspends the run (status 'waiting') until an external system delivers an "
            "event with a matching correlation id via POST /graph-workflows/events/"
            "{correlationId} (authenticated). Resumes with the delivered payload as the "
            "node output. Covers real async systems: payments, digital signatures, "
            "tickets, third-party callbacks. 'On timeout' routes to the 'timeout' "
            "branch, or fails the node. A waiting run does not occupy a concurrency slot."
        ),
        params_schema=[
            _param("correlationId", "Correlation id (expression)", "expression"),
            _param("timeout", "Timeout (seconds, default 86400, max 7 days)", "number"),
            _param("onTimeout", "On timeout", "select", options=["branch", "fail"]),
        ],
    ),
    # ── action: chatbot reply (Phase 41 — roadmap fase 9.3) ──
    NodeTypeInfo(
        type="chat.reply", category="action", label="Chat reply", outputs=["main"],
        description=(
            "Terminal node for a 'Chat' workflow: its resolved text becomes the "
            "reply returned to the conversation (POST /{id}/chat). Also appends "
            "to the session history. Defaults to this node's input when 'text' is "
            "empty. Output: {reply}."
        ),
        params_schema=[_param("text", "Reply text (expression, defaults to input)", "expression")],
    ),
    # ── notify ──
    NodeTypeInfo(
        type="notify.telegram", category="notify", label="Telegram", outputs=["main"],
        description=(
            "Sends a message to the Telegram chat linked to this profile (Settings → "
            "Telegram). Fails if no chat is linked; muted chats are a silent no-op. "
            "Pick a parse mode to render Markdown/HTML formatting instead of plain text "
            "(CommonMark **bold** is normalised to Telegram's single-asterisk bold)."
        ),
        params_schema=[
            _param("text", "Message (expression)", "expression"),
            _param(
                "parse_mode", "Parse mode", "select",
                options=["", "Markdown", "MarkdownV2", "HTML"],
                hint="Plain text, or Telegram formatting (bold/italic/links/…)",
            ),
        ],
    ),
    NodeTypeInfo(
        type="notify.email", category="notify", label="Email", outputs=["main"],
        description=(
            "Sends a plain-text email via the configured SMTP server (SMTP_* settings). "
            "Fails when SMTP is not configured, so retry / On error apply."
        ),
        params_schema=[
            _param("to", "To (comma-separated)", "text"),
            _param("subject", "Subject (expression)", "expression"),
            _param("body", "Body (expression)", "expression"),
        ],
    ),
    NodeTypeInfo(
        type="notify.webhook", category="notify", label="Webhook out", outputs=["main"],
        description=(
            "POSTs a JSON payload to an external webhook URL (Slack/Discord/ntfy/…). "
            "Defaults to this node's input when 'payload' is empty."
        ),
        params_schema=[
            _param("url", "URL (expression)", "expression"),
            _param("payload", "Payload (JSON)", "json"),
            _param("headers", "Headers (JSON object)", "json"),
        ],
    ),
    NodeTypeInfo(
        type="notify.inapp", category="notify", label="In-app", outputs=["main"],
        description=(
            "Pushes a notification to the web UI bell (persisted, live over SSE). "
            "Works with zero configuration."
        ),
        params_schema=[
            _param("title", "Title", "text"),
            _param("body", "Body (expression)", "expression"),
        ],
    ),
    # ── ai: knowledge-base bridge (Phase 38 — roadmap fase 6.5) ──
    NodeTypeInfo(
        type="kb.search", category="ai", label="KB Search", outputs=["main"],
        description=(
            "Semantic search over the profile's knowledge base (workspace documents). "
            "Output: {results: [{text, score, source, chunk_index}], count} — RAG "
            "inside workflows without going through a generic LLM agent."
        ),
        params_schema=[
            _param("query", "Query (expression, defaults to node input)", "expression"),
            _param("top_k", "Top K (default 5, max 20)", "number"),
            _param("document_ids", "Document filter (JSON array or comma-separated ids)", "json"),
        ],
    ),
    # ── ai ──
    NodeTypeInfo(
        type="llm.completion", category="ai", label="LLM Completion", outputs=["main"],
        description="A single provider chat completion.",
        params_schema=[
            _param("model", "Model", "model"),
            _param("system", "System prompt", "code"),
            _param("prompt", "Prompt (expression)", "expression"),
            _param(
                "failover_chain", "Failover chain", "model-chain",
                hint="On call failure, retries in order through this named chain (Settings → Models)",
            ),
            *_ab_params(),
        ],
        defaults={"retry": 1, "backoff": 2, "backoffStrategy": "exponential", "timeoutMs": 120000},
    ),
    NodeTypeInfo(
        type="llm.agent", category="ai", label="LLM Agent", outputs=["main"],
        description="Runs the durable Phase 18 agent loop (goal + full tool registry, incl. MCP + custom) to completion.",
        params_schema=[
            _param("model", "Model", "model"),
            _param("goal", "Goal (expression)", "expression"),
            _param("max_steps", "Max steps", "number"),
            _param(
                "failover_chain", "Failover chain", "model-chain",
                hint="On call failure, retries in order through this named chain (Settings → Models)",
            ),
        ],
        defaults={"retry": 1, "backoff": 2, "backoffStrategy": "exponential", "timeoutMs": 300000},
    ),
    # ── ai: structured-output nodes (Phase 35 — roadmap fase 4.1) ──
    NodeTypeInfo(
        type="llm.classify", category="ai", label="LLM Classify", outputs=["main"],
        description=(
            "Classifies the input into one of the allowed categories with guaranteed "
            "structure: a reply outside the list raises (so retry / On error apply). "
            "Output: {category, confidence}."
        ),
        params_schema=[
            _param("model", "Model", "model"),
            _param("input", "Input (expression, defaults to node input)", "expression"),
            _param("categories", "Categories (JSON array or comma-separated)", "json"),
            _param("instructions", "Extra instructions", "text"),
            _param(
                "failover_chain", "Failover chain", "model-chain",
                hint="On call failure, retries in order through this named chain (Settings → Models)",
            ),
            *_ab_params(),
        ],
        defaults={"retry": 1, "backoff": 2, "backoffStrategy": "exponential", "timeoutMs": 120000},
    ),
    NodeTypeInfo(
        type="llm.extract", category="ai", label="LLM Extract", outputs=["main"],
        description=(
            "Extracts structured data matching a JSON Schema declared in the inspector. "
            "Missing required properties raise (so retry / On error apply). Output: {data}."
        ),
        params_schema=[
            _param("model", "Model", "model"),
            _param("input", "Input (expression, defaults to node input)", "expression"),
            _param("schema", "JSON Schema (object)", "json"),
            _param("instructions", "Extra instructions", "text"),
            _param(
                "failover_chain", "Failover chain", "model-chain",
                hint="On call failure, retries in order through this named chain (Settings → Models)",
            ),
            *_ab_params(),
        ],
        defaults={"retry": 1, "backoff": 2, "backoffStrategy": "exponential", "timeoutMs": 120000},
    ),
    # ── ai: quality gate (Phase 50 — roadmap fase 18.1) ──
    NodeTypeInfo(
        type="llm.judge", category="ai", label="LLM Judge", outputs=["pass", "fail"],
        description=(
            "Scores another node's output against a rubric on a 1..scaleMax scale and routes "
            "to the pass / fail handle by a threshold (default 60% of the scale). Enables "
            "generate → judge → regenerate loops and quality gates before publishing. "
            "Output: {score, verdict, passed, rationale}."
        ),
        params_schema=[
            _param("model", "Model", "model"),
            _param("input", "Content to judge (expression, defaults to node input)", "expression"),
            _param("criteria", "Criteria / rubric", "code"),
            _param("reference", "Reference answer (expression, optional)", "expression"),
            _param("scaleMax", "Score scale max (1..N)", "number"),
            _param("threshold", "Pass threshold (score ≥)", "number"),
            _param("instructions", "Extra instructions", "text"),
            _param(
                "failover_chain", "Failover chain", "model-chain",
                hint="On call failure, retries in order through this named chain (Settings → Models)",
            ),
            *_ab_params(),
        ],
        defaults={"retry": 1, "backoff": 2, "backoffStrategy": "exponential", "timeoutMs": 120000},
    ),
    # ── Phase 47 (roadmap fase 15) — connectors and multimodal nodes ──
    # 15.1 curated connector library — prebuilt integrations over http.request
    # with auth wired to $secrets. Adding a service is one registry entry in the
    # engine; each op carries the http.request retry preset for transient errors.
    NodeTypeInfo(
        type="connector.slack.postMessage", category="connector", label="Slack: post message",
        outputs=["main"],
        description=(
            "Posts a message to a Slack channel via chat.postMessage. Token from "
            "$secrets (chat:write). Output: the http.request output + {operation}."
        ),
        params_schema=[
            _param("token", "Bot token (={{ $secrets.SLACK_TOKEN }})", "expression"),
            _param("channel", "Channel id / name", "expression"),
            _param("text", "Message text", "expression"),
            _param("thread_ts", "Reply in thread (ts, optional)", "expression"),
        ],
        defaults={"retry": 2, "backoff": 2, "backoffStrategy": "exponential", "timeoutMs": 60000},
    ),
    NodeTypeInfo(
        type="connector.discord.postMessage", category="connector", label="Discord: post message",
        outputs=["main"],
        description="Posts a message to a Discord channel via an incoming webhook URL.",
        params_schema=[
            _param("webhook_url", "Webhook URL (={{ $secrets.DISCORD_WEBHOOK }})", "expression"),
            _param("text", "Message content", "expression"),
            _param("username", "Override username (optional)", "expression"),
        ],
        defaults={"retry": 2, "backoff": 2, "backoffStrategy": "exponential", "timeoutMs": 60000},
    ),
    NodeTypeInfo(
        type="connector.github.createIssue", category="connector", label="GitHub: create issue",
        outputs=["main"],
        description="Opens an issue on a GitHub repo (owner/repo). Token from $secrets.",
        params_schema=[
            _param("token", "Token (={{ $secrets.GITHUB_TOKEN }})", "expression"),
            _param("repo", "owner/repo", "expression"),
            _param("title", "Title", "expression"),
            _param("body", "Body", "expression"),
            _param("labels", "Labels (JSON array)", "json"),
        ],
        defaults={"retry": 2, "backoff": 2, "backoffStrategy": "exponential", "timeoutMs": 60000},
    ),
    NodeTypeInfo(
        type="connector.gitlab.createIssue", category="connector", label="GitLab: create issue",
        outputs=["main"],
        description="Opens an issue on a GitLab project. PRIVATE-TOKEN from $secrets.",
        params_schema=[
            _param("token", "Token (={{ $secrets.GITLAB_TOKEN }})", "expression"),
            _param("project", "Project (id or group/path)", "expression"),
            _param("base_url", "Base URL (default https://gitlab.com)", "text"),
            _param("title", "Title", "expression"),
            _param("body", "Description", "expression"),
            _param("labels", "Labels (comma-separated)", "expression"),
        ],
        defaults={"retry": 2, "backoff": 2, "backoffStrategy": "exponential", "timeoutMs": 60000},
    ),
    NodeTypeInfo(
        type="connector.jira.createIssue", category="connector", label="Jira: create issue",
        outputs=["main"],
        description="Creates a Jira issue (Cloud REST v3, Basic auth email:token from $secrets).",
        params_schema=[
            _param("base_url", "Base URL (https://your.atlassian.net)", "text"),
            _param("email", "Account email (={{ $secrets.JIRA_EMAIL }})", "expression"),
            _param("token", "API token (={{ $secrets.JIRA_TOKEN }})", "expression"),
            _param("project_key", "Project key", "expression"),
            _param("summary", "Summary", "expression"),
            _param("issue_type", "Issue type (default Task)", "text"),
            _param("description", "Description (ADF object)", "json"),
        ],
        defaults={"retry": 2, "backoff": 2, "backoffStrategy": "exponential", "timeoutMs": 60000},
    ),
    NodeTypeInfo(
        type="connector.sheets.append", category="connector", label="Google Sheets: append",
        outputs=["main"],
        description="Appends rows to a Google Sheet (values.append). OAuth token from $secrets.",
        params_schema=[
            _param("token", "OAuth token (={{ $secrets.GOOGLE_TOKEN }})", "expression"),
            _param("spreadsheet_id", "Spreadsheet id", "expression"),
            _param("range", "Range (default Sheet1!A1)", "text"),
            _param("values", "Rows (JSON array of arrays)", "json"),
        ],
        defaults={"retry": 2, "backoff": 2, "backoffStrategy": "exponential", "timeoutMs": 60000},
    ),
    NodeTypeInfo(
        type="connector.sheets.read", category="connector", label="Google Sheets: read",
        outputs=["main"],
        description="Reads a range from a Google Sheet (values.get). Output json.values holds the rows.",
        params_schema=[
            _param("token", "OAuth token (={{ $secrets.GOOGLE_TOKEN }})", "expression"),
            _param("spreadsheet_id", "Spreadsheet id", "expression"),
            _param("range", "Range (default Sheet1!A1:Z1000)", "text"),
        ],
        defaults={"retry": 2, "backoff": 2, "backoffStrategy": "exponential", "timeoutMs": 60000},
    ),
    # 15.2 ssh.exec
    NodeTypeInfo(
        type="ssh.exec", category="action", label="SSH exec", outputs=["main"],
        description=(
            "Runs a command on a remote host over SSH (key or password from "
            "$secrets; host allow-list per GRAPH_WORKFLOW_SSH_ALLOWED_HOSTS). "
            "Output: {stdout, stderr, exit_code}. Non-zero exit raises (so retry / "
            "On error apply) unless 'Allow non-zero exit' is set."
        ),
        params_schema=[
            _param("host", "Host", "expression"),
            _param("port", "Port (default 22)", "number"),
            _param("username", "Username", "expression"),
            _param("password", "Password (={{ $secrets.SSH_PASSWORD }})", "expression"),
            _param("private_key", "Private key (={{ $secrets.SSH_KEY }})", "expression"),
            _param("command", "Command", "expression"),
            _param("timeout", "Timeout (seconds)", "number"),
            _param("allow_nonzero", "Allow non-zero exit (true/false)", "text"),
        ],
        defaults={"retry": 1, "backoff": 2, "backoffStrategy": "exponential", "timeoutMs": 60000},
    ),
    # 15.3 browser (Playwright)
    NodeTypeInfo(
        type="browser", category="action", label="Browser", outputs=["main"],
        description=(
            "Headless-browser scraping/checks (Playwright): open a URL, optionally "
            "wait for a selector, then extract text / an attribute / a screenshot "
            "(saved to the workspace storage). Requires playwright in the image."
        ),
        params_schema=[
            _param("url", "URL (expression)", "expression"),
            _param("action", "Action", "select", options=["text", "attribute", "screenshot"]),
            _param("selector", "CSS selector (optional)", "text"),
            _param("attribute", "Attribute (for action=attribute)", "text"),
            _param("screenshot_path", "Screenshot path (for action=screenshot)", "text"),
            _param("timeout", "Timeout (seconds)", "number"),
        ],
        defaults={"retry": 1, "backoff": 2, "backoffStrategy": "exponential", "timeoutMs": 60000},
    ),
    # 15.5 doc.convert (multimodal)
    NodeTypeInfo(
        type="doc.convert", category="data", label="Doc → Markdown", outputs=["main"],
        description=(
            "Converts a PDF/DOCX/HTML/PPTX/… document from the workspace storage "
            "to markdown via markitdown. Output: {markdown, chars, path}. Path "
            "defaults to the node input (e.g. a file.watch $trigger.path)."
        ),
        params_schema=[
            _param("path", "Path (expression, defaults to input)", "expression"),
        ],
    ),

    # ── Phase 48 (roadmap fase 16.1) — persistent state across runs ──
    NodeTypeInfo(
        type="state.get", category="data", label="State: get", outputs=["main"],
        description=(
            "Reads a key from the workflow's persistent key/value store (survives "
            "across runs). Output: {key, value, found}. `default` is returned when "
            "the key is missing or expired."
        ),
        params_schema=[
            _param("key", "Key", "expression"),
            _param("default", "Default (when missing)", "expression"),
        ],
    ),
    NodeTypeInfo(
        type="state.set", category="data", label="State: set", outputs=["main"],
        description=(
            "Writes a key to the workflow's persistent store. `value` defaults to "
            "the node input. `ttlSeconds` > 0 gives the key an expiry. Output: "
            "{key, value}."
        ),
        params_schema=[
            _param("key", "Key", "expression"),
            _param("value", "Value (expression, defaults to input)", "expression"),
            _param("ttlSeconds", "TTL seconds (0/empty = never)", "number"),
        ],
    ),
    NodeTypeInfo(
        type="state.increment", category="data", label="State: increment", outputs=["main"],
        description=(
            "Atomically adds `amount` (default 1) to a numeric key (0 when missing) "
            "and returns the new value. Ideal for counters and rate windows. "
            "Output: {key, value}."
        ),
        params_schema=[
            _param("key", "Key", "expression"),
            _param("amount", "Amount (default 1)", "number"),
            _param("ttlSeconds", "TTL seconds (0/empty = never)", "number"),
        ],
    ),

    # ── Phase 52 (roadmap fase 20) — Telegram as a workflow channel ──
    NodeTypeInfo(
        type="telegram", category="trigger", label="Telegram", inputs=0, outputs=["main"],
        description=(
            "Inbound Telegram trigger (fase 20.1). Bind a bot command to this "
            "workflow under Settings → Telegram, or launch it with /run in chat. "
            "$trigger = {chat_id, thread_id, user, text, command, args, file?}."
        ),
        params_schema=[_param("command", "Bot command (optional, e.g. report)", "text")],
    ),
    NodeTypeInfo(
        type="telegram.send", category="notify", label="Telegram: send", outputs=["main"],
        description=(
            "Sends a text message to a chat (fase 20.2). `chat_id` defaults to the "
            "originating chat ($trigger.chat_id). Output: {sent, message_id, chat_id}."
        ),
        params_schema=[
            _param("chat_id", "Chat id (expression, defaults to $trigger.chat_id)", "expression"),
            _param("text", "Text (expression, defaults to input)", "expression"),
            _param("thread_id", "Thread id (forum topic, optional)", "expression"),
            _param("reply_to", "Reply to message id (optional)", "expression"),
            _param("parse_mode", "Parse mode", "select", options=["", "Markdown", "MarkdownV2", "HTML"]),
            _param("disable_preview", "Disable link preview", "boolean"),
        ],
    ),
    NodeTypeInfo(
        type="telegram.sendMedia", category="notify", label="Telegram: send media", outputs=["main"],
        description=(
            "Sends a photo/document/audio/voice/video from workspace storage or a "
            "URL, with an optional caption (fase 20.2)."
        ),
        params_schema=[
            _param("chat_id", "Chat id (defaults to $trigger.chat_id)", "expression"),
            _param("media_type", "Media type", "select",
                   options=["document", "photo", "audio", "voice", "video"]),
            _param("path", "Workspace path", "expression"),
            _param("url", "URL (instead of a path)", "expression"),
            _param("caption", "Caption", "expression"),
        ],
    ),
    NodeTypeInfo(
        type="telegram.editMessage", category="notify", label="Telegram: edit", outputs=["main"],
        description="Edits a message sent earlier in the run (progress → done) (fase 20.2).",
        params_schema=[
            _param("chat_id", "Chat id (defaults to $trigger.chat_id)", "expression"),
            _param("message_id", "Message id", "expression"),
            _param("text", "New text", "expression"),
            _param("parse_mode", "Parse mode", "select", options=["", "Markdown", "MarkdownV2", "HTML"]),
        ],
    ),
    NodeTypeInfo(
        type="telegram.deleteMessage", category="notify", label="Telegram: delete", outputs=["main"],
        description="Deletes a message sent earlier in the run (fase 20.2).",
        params_schema=[
            _param("chat_id", "Chat id (defaults to $trigger.chat_id)", "expression"),
            _param("message_id", "Message id", "expression"),
        ],
    ),
    NodeTypeInfo(
        type="telegram.ask", category="action", label="Telegram: ask", outputs=["main", "timeout"],
        description=(
            "Presents inline buttons on Telegram and suspends the run until the "
            "user taps one; resumes with {value} down 'main' (fase 20.3). A timeout "
            "follows 'timeout' unless onTimeout=fail."
        ),
        params_schema=[
            _param("chat_id", "Chat id (defaults to $trigger.chat_id)", "expression"),
            _param("text", "Question", "expression"),
            _param("options", "Options (JSON [{label, value}])", "json"),
            _param("timeout", "Timeout seconds (default 3600)", "number"),
            _param("onTimeout", "On timeout", "select", options=["branch", "fail"]),
        ],
    ),
]


def _tool_node(tool: dict, category: str) -> NodeTypeInfo | None:
    fn = tool.get("function", {})
    name = fn.get("name")
    if not name:
        return None
    props = (fn.get("parameters") or {}).get("properties") or {}
    required = set((fn.get("parameters") or {}).get("required") or [])
    params_schema = [
        _param(
            pname,
            pname + (" *" if pname in required else ""),
            "expression",
            hint=(pinfo or {}).get("description", ""),
        )
        for pname, pinfo in props.items()
    ]
    # For MCP tools (mcp__server__tool) show a friendlier label than the raw name.
    label = name.split("__", 2)[-1] if name.startswith(("mcp__", "custom__")) else name
    return NodeTypeInfo(
        type=f"tool.{name}",
        category=category,
        label=label,
        description=fn.get("description", ""),
        params_schema=params_schema,
    )


async def node_catalog(db=None, profile_id: str = "default") -> list[NodeTypeInfo]:
    """Full palette: static nodes + one ``tool.<name>`` per registry tool.

    When a ``db`` connection is passed the catalog also includes **discovered MCP
    server tools** and the profile's **custom HTTP tools** (category ``mcp``), so
    any MCP/custom tool becomes a drag-in node — the engine's ``tool.<name>``
    executor already routes ``mcp__*`` / ``custom__*`` names via ``execute_tool``.
    """
    from app.tools.registry import TOOL_DEFINITIONS

    catalog = list(_STATIC_NODES)
    for tool in TOOL_DEFINITIONS:
        node = _tool_node(tool, "action")
        if node:
            catalog.append(node)

    if db is not None:
        from app.services import custom_tool_service, mcp_service

        try:
            await mcp_service.refresh(db)
            for tool in mcp_service.get_tool_definitions():
                node = _tool_node(tool, "mcp")
                if node:
                    catalog.append(node)
        except Exception:  # noqa: BLE001 — a broken MCP server must not blank the palette
            pass
        try:
            for tool in await custom_tool_service.get_tool_definitions(db, profile_id):
                node = _tool_node(tool, "mcp")
                if node:
                    catalog.append(node)
        except Exception:  # noqa: BLE001
            pass

        # Fase 6.4 — workflows that declare an input contract become typed,
        # directly-callable nodes: the node params mirror the contract's
        # properties and the engine routes workflow.<id> through the
        # subworkflow executor (input/output validated against the contracts).
        try:
            from app.db import graph_workflow_repository as wf_repo

            for wf in await wf_repo.list_callable_workflows(db, profile_id):
                schema = wf.input_schema or {}
                props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
                required = set(schema.get("required") or [])
                params_schema = [
                    _param(
                        pname,
                        pname + (" *" if pname in required else ""),
                        "expression",
                        hint=str((pinfo or {}).get("description") or ""),
                    )
                    for pname, pinfo in props.items()
                ]
                catalog.append(NodeTypeInfo(
                    type=f"workflow.{wf.id}",
                    category="action",
                    label=wf.name,
                    description=(wf.description or f"Calls the '{wf.name}' workflow")
                    + " (typed subworkflow — input/output validated against its contracts).",
                    params_schema=params_schema,
                ))
        except Exception:  # noqa: BLE001 — a repo hiccup must not blank the palette
            pass

        # Fase 19 — installed custom nodes (declarative or python) become
        # first-class palette entries. Their inspector params come from the
        # manifest's `params` JSON Schema; `custom: True` badges them in the UI.
        try:
            from app.db import graph_workflow_repository as wf_repo

            for node in await wf_repo.list_custom_nodes(db, profile_id, enabled_only=True):
                manifest = node.get("manifest") or {}
                schema = manifest.get("params") if isinstance(manifest.get("params"), dict) else {}
                props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
                required = set(schema.get("required") or [])
                params_schema = [
                    _param(
                        pname,
                        pname + (" *" if pname in required else ""),
                        "expression",
                        hint=str((pinfo or {}).get("description") or ""),
                    )
                    for pname, pinfo in props.items()
                ]
                catalog.append(NodeTypeInfo(
                    type=node["type"],
                    category=node.get("category") or "custom",
                    label=node.get("name") or node["type"],
                    description=(node.get("description") or "")
                    + f" (custom {node['kind']} node v{node['version']}).",
                    outputs=manifest.get("handles") or ["main"],
                    params_schema=params_schema,
                    custom=True,
                ))
        except Exception:  # noqa: BLE001 — a broken custom node must not blank the palette
            pass

    return catalog
