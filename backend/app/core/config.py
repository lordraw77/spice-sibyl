"""
Application settings loaded from environment variables / .env file via pydantic-settings.

All fields can be overridden at runtime by setting the corresponding environment variable
(e.g. DEFAULT_MODEL, GROQ_API_KEY).  The lru_cache on get_settings() ensures a single
Settings instance is created for the lifetime of the process.
"""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Fallback release version when APP_VERSION isn't stamped into the build.
_DEFAULT_VERSION = '3.8.0'


class Settings(BaseSettings):
    # General service configuration
    app_name: str = 'SpiceSibyl API'
    # Release version surfaced by GET /v1/info and the FastAPI docs. Stamped by
    # the Docker build from the git tag (--build-arg APP_VERSION); an empty env
    # value falls back to the default below.
    app_version: str = _DEFAULT_VERSION
    app_env: str = 'development'

    @field_validator('app_version')
    @classmethod
    def _version_fallback(cls, value: str) -> str:
        return value.strip().lstrip('v') or _DEFAULT_VERSION
    app_debug: bool = True
    app_host: str = '0.0.0.0'
    app_port: int = 8000

    # Simple bearer token used to authenticate incoming API requests
    api_key: str = 'change-me'

    # Comma-separated list of allowed CORS origins
    cors_origins: str = 'http://localhost:4200,http://127.0.0.1:4200'

    # Public URL for DDNS / reverse-proxy access (e.g. https://sibyl.example.com).
    # Automatically added to cors_origins so both local dev and external access work.
    public_url: str | None = None

    # Model selected when the caller does not specify one
    default_model: str = 'ollama/qwen2.5:7b-instruct'

    # Set to "mock" to bypass real providers during testing
    litellm_provider: str = 'litellm'

    # Base URL for the local Ollama instance (host.docker.internal resolves inside Docker)
    ollama_api_base: str = 'http://host.docker.internal:11434'

    # Automatic model-catalog discovery refresh (0 disables the background loop)
    discovery_refresh_enabled: bool = True
    discovery_refresh_hours: float = 12.0

    # Provider API keys — None means the provider is unconfigured / disabled
    openai_api_key: str = 'dummy'
    groq_api_key: str | None = None
    openrouter_api_key: str | None = None
    gemini_api_key: str | None = None
    cloudflare_api_key: str | None = None
    cloudflare_account_id: str | None = None
    together_api_key: str | None = None
    fireworks_api_key: str | None = None
    mistral_api_key: str | None = None
    hf_token: str | None = None
    cerebras_api_key: str | None = None
    nvidia_api_key: str | None = None

    # Multi-MCP orchestrator sidecar (agent/* models). Empty = disabled.
    # e.g. http://host.docker.internal:8910/v1
    orchestrator_base_url: str | None = None
    # Read timeout (s) for an orchestrator turn — it spawns Docker MCP sub-agents.
    orchestrator_timeout: float = 300.0

    # Phase 18: log every MCP tool call (server, tool, arguments) and the raw
    # tools/call result. Set MCP_LOG_CALLS=false to silence on noisy servers.
    mcp_log_calls: bool = True
    # Truncate the logged raw output to this many chars (0 = no truncation).
    mcp_log_max_chars: int = 4000

    # Phase 23.5.c: stdio guardrails. Spawning arbitrary commands from the admin
    # UI is powerful, so it can be switched off entirely, and is otherwise gated
    # by an allowlist of command basenames (comma-separated). `mcp-proxy` is
    # included because it's the standard stdio-to-remote-SSE/streamable-HTTP
    # bridge (e.g. wrapping a Home Assistant MCP endpoint) referenced elsewhere
    # in the docs as a supported deployment path.
    mcp_stdio_enabled: bool = True
    mcp_allowed_commands: str = 'docker,npx,uvx,uv,python,node,mcp-proxy'

    # --- Phase 18: sandboxed code interpreter (python_exec built-in tool) ---
    # Runs model-supplied Python in an isolated subprocess with resource limits
    # and no network. Set CODE_INTERPRETER_ENABLED=false to remove the tool.
    code_interpreter_enabled: bool = True
    # Wall-clock timeout (s) for one execution; also used as the CPU-seconds limit.
    code_interpreter_timeout: float = 20.0
    # Address-space (memory) cap for the sandbox process.
    code_interpreter_memory_mb: int = 512
    # Truncate captured stdout/stderr to this many chars each.
    code_interpreter_max_output_chars: int = 8000

    # Max agent-loop iterations for tool calls within a single chat turn
    # (CHAT_MAX_TOOL_ITERATIONS). For longer loops use workflows.
    chat_max_tool_iterations: int = 5

    # --- Phase 18: persistent multi-step workflows (agent runs) ---
    # Default / hard cap on agent-loop iterations for a workflow run.
    workflow_default_max_steps: int = 20
    workflow_max_steps_limit: int = 100

    # --- Phase 29: visual node-graph workflow engine ---
    # Enable the schedule-trigger polling loop (started at app startup).
    graph_workflow_scheduler_enabled: bool = True
    # Hard cap on nodes per graph to bound a single run.
    graph_workflow_max_nodes: int = 200

    # --- Phase 30: workflow hardening ---
    # A schedule/event trigger auto-disables (and raises an in-app alert) after
    # this many consecutive firing failures.
    graph_workflow_trigger_max_failures: int = 5
    # Max nodes executed concurrently within a single run wave.
    graph_workflow_max_concurrent_nodes: int = 8
    # A workflow raises an in-app alert after this many consecutive failed runs.
    graph_workflow_run_failure_alert_threshold: int = 3

    # --- Phase 33: engine reliability (roadmap fase 2) ---
    # At startup, resume runs left 'running'/'pending' by a crash from their
    # checkpointed node outputs, and re-evaluate per-workflow queued runs.
    graph_workflow_resume_on_startup: bool = True

    # --- Phase 35: new nodes & capabilities (roadmap fase 4) ---
    # Workspace storage root for the file.read/file.write nodes and sqlite
    # db.query databases. Every path a node uses is resolved INSIDE this
    # directory (traversal outside it is rejected). Created on first use.
    graph_workflow_files_dir: str = "data/workflow_files"
    # A human.approval node waits at most this many seconds even when the node
    # asks for a longer timeout (default cap: 7 days).
    graph_workflow_approval_max_timeout: int = 604800

    # --- Phase 38: engine extension (roadmap fase 6) ---
    # Hard cap on `while` node iterations; the node's own maxIterations
    # (default 100) can never exceed this (GRAPH_WORKFLOW_WHILE_MAX_ITERATIONS).
    graph_workflow_while_max_iterations: int = 1000
    # Global per-host rate limits for http.request / notify.webhook nodes:
    # "host=rpm" pairs separated by commas (e.g. "api.github.com=30,slack.com=50")
    # or a JSON object {"host": rpm}. Requests over the threshold wait, they
    # don't fail; the wait shows up as `rate_limited_s` in the node output.
    graph_workflow_rate_limits: str = ""
    # Poll interval (seconds) for file.watch / email.inbound triggers. Each
    # trigger may set a slower per-trigger `interval` in its config; this value
    # is the floor.
    graph_workflow_watch_poll_seconds: int = 60

    # --- Phase 40: advanced editor (roadmap fase 8) ---
    # Step-debug session timeout (seconds): a run left ``paused`` in the debugger
    # longer than this is auto-cancelled by the scheduler sweep, so a forgotten
    # debug session doesn't stay suspended forever (GRAPH_WORKFLOW_DEBUG_MAX_PAUSE).
    graph_workflow_debug_max_pause: int = 3600

    # --- Phase 41: workflows as ecosystem tools (roadmap fase 9) ---
    # An active workflow with an input contract and `expose_as_tool` becomes an
    # invocable tool (for llm.agent, other workflows' tool.* nodes, the product
    # chat and the MCP server). This caps the depth of a tool→workflow→tool chain
    # so a workflow that calls itself (directly or transitively) cannot recurse
    # forever (GRAPH_WORKFLOW_TOOL_MAX_DEPTH).
    graph_workflow_tool_max_depth: int = 3
    # `chat` trigger (fase 9.3): a conversation session is retained this many
    # seconds after its last message; idle sessions are purged by the scheduler
    # sweep. 0 disables expiry (GRAPH_WORKFLOW_CHAT_SESSION_TTL).
    graph_workflow_chat_session_ttl: int = 86400
    # Most conversation turns kept in a chat session's history (older turns are
    # trimmed before the workflow sees `$trigger.history`).
    graph_workflow_chat_history_max_turns: int = 20
    # OpenAPI import (fase 9.4): the most operations turned into http.request
    # nodes from a single spec, so a huge API can't blow up the palette/graph.
    graph_workflow_openapi_max_operations: int = 100

    # --- Phase 44: data and budget governance (roadmap fase 12) ---
    # Fraction of a token/run budget (workflow or profile-wide) that triggers a
    # one-time in-app soft warning for the current period (GRAPH_WORKFLOW_BUDGET_WARN_PCT).
    graph_workflow_budget_warn_pct: float = 0.8
    # Global default run/node-run retention in days (0 = keep forever). A
    # workflow's own `runs_retention_days` overrides this; the scheduler sweep
    # purges terminal (completed/failed/cancelled) runs past the cutoff.
    graph_workflow_runs_retention_days: int = 0

    # --- Phase 45: copilot and workflow-as-code (roadmap fase 13) ---
    # Local working copy root for the fase 13.3 Git sync of workflow
    # definitions — one clone per workflow at <dir>/<workflow_id>. Created on
    # first push/pull; the token in git_token_secret is injected into the
    # remote URL only for the duration of each git subprocess call.
    graph_workflow_git_workdir: str = "data/git_sync"
    graph_workflow_git_timeout_seconds: int = 30

    # --- Phase 46: remote execution and scalability (roadmap fase 14) ---
    # 14.1 — remote runners. A runner is considered offline once its last
    # heartbeat is older than this many seconds; the Runners page and the
    # dispatcher both use it. Job dispatch to a runner waits up to
    # `graph_workflow_runner_job_timeout` seconds for a result before applying
    # the node's `runOnFallback` (fail | local).
    graph_workflow_runner_heartbeat_timeout: int = 90
    graph_workflow_runner_job_timeout: int = 120
    graph_workflow_runner_poll_interval: float = 1.0
    # 14.2 — the `code` node always runs through the Phase 18 sandboxed
    # subprocess (CPU/memory/time-limited, no network); these mirror the
    # existing code-interpreter limits so a remote runner enforces the same
    # bounds. See CODE_INTERPRETER_* for the actual values used.
    # 14.3 — engine scale-out. A run is "leased" to the process instance that
    # is executing it (owner id + expiry on the run row); a heartbeat renews it
    # while the run is active and a stale lease (past its expiry, e.g. after a
    # crash) is taken over on the next startup/resume sweep. Generic — a no-op
    # in a single-process deployment; true multi-replica coordination needs a
    # shared DB with row-level locking (Postgres), not yet required by SQLite.
    graph_workflow_lease_ttl_seconds: int = 45
    # 14.4 — message queue triggers. `memory` = per-process asyncio queue (lost
    # on restart, zero setup — good for tests/dev); `db` = persisted in the
    # `workflow_queue_messages` table (survives restarts, still no external
    # broker). A real broker (RabbitMQ/Kafka/MQTT) plugs in as another
    # `QueueDriver` implementation — see workflow_graph_service.QueueDriver.
    graph_workflow_queue_driver: str = "db"
    graph_workflow_queue_poll_seconds: int = 5

    # --- Phase 47 (roadmap fase 15) — connectors and multimodal nodes ---
    # `ssh.exec` node: comma-separated host allow-list (empty = allow any host);
    # a per-command wall-clock timeout guards against hung sessions.
    graph_workflow_ssh_allowed_hosts: str = ""
    graph_workflow_ssh_timeout_seconds: int = 30
    # `browser` node (Playwright): per-action wall-clock timeout. Requires the
    # `playwright` package + a browser in the backend image; the node raises a
    # clear error when it is absent instead of silently degrading.
    graph_workflow_browser_timeout_seconds: int = 30
    # `rss.read` trigger: max new entries fired per poll (newest first), so a
    # feed that publishes a burst never storms the engine.
    graph_workflow_rss_max_entries: int = 20

    # --- Phase 48 (roadmap fase 16) — state and execution semantics ---
    # `state.set`/`state.increment` nodes: default TTL (seconds) applied to a key
    # when the node leaves `ttlSeconds` unset. 0 = keys never expire by default.
    graph_workflow_state_default_ttl_seconds: int = 0
    # Trigger idempotency (webhook/event): default dedup window (seconds) used
    # when a trigger sets a `dedupKey` but no `dedupWindowSeconds`. Deliveries of
    # the same key inside this window return the original run instead of a new one.
    graph_workflow_dedup_default_window_seconds: int = 3600

    # --- Phase 50 (roadmap fase 18) — LLM quality ---
    # `llm.judge`: score scale used when the node leaves `scaleMax` unset (1..N,
    # higher is better). The pass/fail threshold defaults to 60% of this scale.
    graph_workflow_judge_default_scale_max: int = 5

    # --- Phase 51 (roadmap fase 19) — Custom Node SDK ---
    # Directory holding uploaded custom-node packages (one subdir per node type /
    # version, storing the manifest and, for `python` nodes, the module).
    graph_workflow_custom_nodes_dir: str = "data/custom_nodes"
    # When true, a package must carry a valid `signature` (HMAC-SHA256 of the
    # manifest+code with GRAPH_WORKFLOW_NODE_SIGNING_KEY) before it can be
    # installed — a workspace hardening switch (roadmap 19.3). Off by default.
    graph_workflow_require_signed_nodes: bool = False
    # Shared secret used to verify (and, in the CLI, produce) package signatures
    # when signing is required. Empty disables verification even if the flag is on.
    graph_workflow_node_signing_key: str = ""

    # --- Phase 52 (roadmap fase 20) — Telegram as a workflow channel ---
    # Max size (MB) of an inbound Telegram file (document/photo/voice/video) a
    # `telegram` trigger will fetch into the workspace storage; larger is dropped.
    graph_workflow_telegram_max_file_mb: int = 20

    # --- SMTP (notify.email workflow node) — leave host empty to disable ---
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    # Sender address; falls back to smtp_user when unset.
    smtp_from: str | None = None
    # STARTTLS on the connection (disable only for trusted local relays).
    smtp_starttls: bool = True

    # SQLite database path for conversation persistence
    db_path: str = "spice_sibyl.db"

    # Size of the shared aiosqlite connection pool (app/db/pool.py). Connections
    # are reused across requests/tasks instead of opened per call. SQLite still
    # has a single writer, so a small pool is plenty; raise it only if read
    # concurrency (WAL) is the bottleneck. This is the seam that later fronts a
    # networked DB (Postgres) without touching call sites.
    db_pool_size: int = 5

    # Telegram bot — leave empty to disable
    telegram_bot_token: str | None = None
    # Comma-separated Telegram user IDs allowed to use the bot (empty = everyone)
    telegram_allowed_users: str | None = None
    # Default model used by the Telegram bot (falls back to default_model)
    telegram_default_model: str | None = None

    # Image generation provider chain — comma-separated "provider:model" pairs
    # tried in order with automatic fallback.  Supported providers:
    # gemini, huggingface, cloudflare, together_ai
    image_generation_chain: str = (
        "gemini:gemini-2.5-flash-image,"
        "gemini:gemini-3.1-flash-image,"
        "gemini:gemini-3-pro-image,"
        "gemini:imagen-4.0-fast-generate-001,"
        "huggingface:black-forest-labs/FLUX.1-schnell,"
        "cloudflare:@cf/stabilityai/stable-diffusion-xl-base-1.0,"
        "together_ai:black-forest-labs/FLUX.1-schnell-Free"
    )

    # Embedding provider chain for RAG — comma-separated "provider:model" pairs
    # tried in order with automatic fallback.  Supported providers:
    # ollama, gemini, mistral.  Ollama is local and free (default first entry).
    embedding_chain: str = (
        "ollama:nomic-embed-text,"
        "gemini:text-embedding-004,"
        "mistral:mistral-embed"
    )

    # --- Phase 17: advanced RAG ---
    # Hybrid retrieval: fuse FTS5 lexical search with vector similarity via
    # Reciprocal Rank Fusion before injecting context. Falls back to pure vector
    # scan when disabled or when the lexical side yields nothing.
    rag_hybrid: bool = True
    # Number of candidates pulled from each retrieval arm (vector + lexical)
    # before fusion / reranking. The final top_k is a subset of this pool.
    rag_candidate_pool: int = 30
    # Reranker applied to the fused candidate pool: "" / "none" (off) or "llm"
    # (ask rag_rerank_model to score relevance). Off by default — opt-in cost.
    rag_rerank: str = ""
    # Model used when rag_rerank == "llm" (provider/model id the gateway can route).
    rag_rerank_model: str = "groq/llama-3.1-8b-instant"

    # --- wikillm: MarkItDown + graph + sqlite-vec ---
    # Fixed dimension of the sqlite-vec ANN table (kb_chunk_vec). Must match the
    # embedding model's output width; changing it requires re-embedding all chunks.
    # nomic-embed-text / gemini text-embedding-004 = 768; mistral-embed = 1024.
    embedding_dim: int = 768
    # Use the sqlite-vec extension for the vector arm when it loads successfully.
    # Falls back to the in-memory numpy cosine scan when the extension is
    # unavailable or fails to load (set False to force the numpy path).
    rag_use_sqlite_vec: bool = True
    # Expand the seed candidate pool by one hop over the knowledge graph
    # (chunks/sections sharing an entity with a seed chunk) before reranking.
    rag_graph_expand: bool = True

    # --- Phase 28.d: GraphRAG (LLM extraction, communities, global search) ---
    # Opt-in LLM entity + relationship extraction at ingest time. When on,
    # graph_service asks an LLM for typed entities and entity→entity relations
    # (richer than the regex heuristic) and adds 'related' edges; it degrades
    # gracefully to the LLM-free extractor on any error or when off.
    graph_llm_extract: bool = False
    # Model for LLM extraction. Empty = default_model. "provider/model" id.
    graph_extract_model: str = ""
    # Cap on the Markdown (chars) sent to the extractor, for cost control.
    graph_extract_max_chars: int = 12000
    # Generate LLM community summaries after a (re)build of the graph. Enables
    # Microsoft-GraphRAG-style global search over the summarised communities.
    graph_community_summary: bool = False
    # Model for community + global-search summarisation. Empty = graph_extract_model,
    # then default_model.
    graph_community_model: str = ""
    # Minimum entity nodes in a community before it is summarised (smaller
    # clusters are folded into their neighbours / ignored for global search).
    graph_community_min_size: int = 3
    # Replace the extractive wiki section summaries with LLM summaries at ingest.
    wiki_llm_summary: bool = False
    # Model for wiki section summaries. Empty = graph_community_model chain.
    wiki_summary_model: str = ""
    # Enable the map-reduce global-search endpoint over community summaries.
    graphrag_global_search: bool = True

    # --- Phase 19: per-profile persistent memory ---
    # Master switch for the memory feature (per-profile toggles live in the DB).
    memory_enabled: bool = True
    # Low-cost model used for the async memory-extraction call after each
    # exchange. Empty = use default_model. "provider/model" id the gateway routes.
    memory_extraction_model: str = ""
    # Char budget for the <user_memory> block injected into the system prompt.
    memory_max_chars: int = 2000
    # Hard cap on stored memories per profile (oldest disabled ones pruned first).
    memory_max_items: int = 100

    # --- Phase 19: LLM auto-titling ---
    # Generate a concise conversation title from the first exchange.
    auto_title_enabled: bool = True
    # Model used for titling. Empty = memory_extraction_model, then default_model.
    title_model: str = ""

    # --- Phase 19: http_request tool ---
    # Optional comma-separated domain-suffix allowlist for the http_request
    # built-in tool (empty = any public host; private IPs are always blocked).
    http_request_allowed_domains: str = ""

    # --- Phase 19: response cache ---
    # Exact-match cache of completed replies (same model/messages/params) to cut
    # latency and cost on repeated queries. In-memory, per-process.
    response_cache_enabled: bool = True
    response_cache_ttl_seconds: int = 600
    response_cache_max_entries: int = 256

    # --- Phase 26: semantic response cache (extends 19.c) ---
    # On an exact-match miss, embed the normalized last user message and replay a
    # cached reply from the same (model, temperature, max_tokens) bucket whose
    # stored embedding is within SEMANTIC_CACHE_THRESHOLD (cosine). Degrades
    # silently to exact-match-only when no embedding provider is reachable.
    semantic_cache_enabled: bool = False
    semantic_cache_threshold: float = 0.92
    # Upper bound on how many of the most-recent cache entries the semantic scan
    # considers per lookup — caps the cosine cost independently of the overall
    # (LRU) cache size.
    semantic_cache_max_entries: int = 256

    # IANA timezone used for Telegram reminder parsing and display (/remind).
    # Keeps reminders correct regardless of the container's system TZ.
    timezone: str = "Europe/Rome"

    # Logging level for the application (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    log_level: str = "INFO"
    # Log output format: "json" for structured logs with request correlation,
    # anything else keeps the human-readable text format used in local dev.
    log_format: str = "text"

    # --- Phase 16: observability & ops ---
    # Optional bearer token guarding GET /metrics. Empty = open (typical when the
    # endpoint is only reachable on an internal network / behind the proxy).
    metrics_token: str | None = None

    # Chat completion fallback chain — comma-separated "provider:model" pairs tried
    # after the requested model when a provider errors/times out *before* emitting
    # any output.  Empty = no fallback (current behaviour).
    chat_fallback_chain: str = ""

    # Scheduled SQLite backups. When enabled, a background task snapshots the DB
    # into backup_dir every backup_interval_hours, keeping the newest
    # backup_retention files.  backup_dir should live on a mounted volume.
    backup_enabled: bool = False
    backup_dir: str = "/data/backups"
    backup_interval_hours: int = 24
    backup_retention: int = 7

    # Master secret used to derive the Fernet encryption key for vaulted API keys.
    # Override with VAULT_SECRET_KEY env var in production.
    vault_secret_key: str = "change-me-in-production"

    # --- Phase 13: authentication, RBAC, rate limiting ---
    # Secret used to sign JWT access/refresh tokens. Override in production.
    jwt_secret_key: str = "change-me-in-production"
    jwt_access_ttl_minutes: int = 30
    jwt_refresh_ttl_days: int = 14

    # Bootstrap admin created on first boot when the users table is empty.
    # Without these, an empty DB with mandatory auth would lock everyone out.
    admin_email: str | None = None
    admin_password: str | None = None

    # Default per-user request rate limit (slowapi syntax, e.g. "60/minute").
    rate_limit_default: str = "60/minute"

    # --- Multi-instance coordination (roadmap v2 § 3, P2) ---
    # Where the sliding windows live. "memory" counts only what this process
    # saw — correct for one instance, and N times too permissive for N of them.
    # "database" shares the window through the rate_limit_hits table.
    rate_limit_backend: str = "memory"
    # Where run events are fanned out. "memory" is single-process; "database"
    # routes them through run_events so an SSE stream served by one instance
    # sees runs executed by another.
    workflow_bus_backend: str = "memory"
    # Only the instance holding the scheduler lease fires due schedules, so N
    # instances do not each start the same run. Disable to let every instance
    # poll (the pre-lease behaviour).
    scheduler_leader_election: bool = True
    # Lease lifetime. A crashed leader is replaced after at most this long, so
    # keep it a small multiple of the poll interval.
    scheduler_lease_ttl_seconds: int = 90

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')


@lru_cache
def get_settings() -> 'Settings':
    """Return the cached application settings singleton."""
    return Settings()


settings = get_settings()
