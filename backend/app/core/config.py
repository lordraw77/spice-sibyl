"""
Application settings loaded from environment variables / .env file via pydantic-settings.

All fields can be overridden at runtime by setting the corresponding environment variable
(e.g. DEFAULT_MODEL, GROQ_API_KEY).  The lru_cache on get_settings() ensures a single
Settings instance is created for the lifetime of the process.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # General service configuration
    app_name: str = 'SpiceSibyl API'
    app_env: str = 'development'
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
