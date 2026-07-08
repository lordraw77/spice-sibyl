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
_DEFAULT_VERSION = '2.0.0'


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

    # SQLite database path for conversation persistence
    db_path: str = "spice_sibyl.db"

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

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')


@lru_cache
def get_settings() -> 'Settings':
    """Return the cached application settings singleton."""
    return Settings()


settings = get_settings()
