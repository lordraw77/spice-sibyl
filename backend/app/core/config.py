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

    # Model selected when the caller does not specify one
    default_model: str = 'ollama/qwen2.5:7b-instruct'

    # Set to "mock" to bypass real providers during testing
    litellm_provider: str = 'litellm'

    # Base URL for the local Ollama instance (host.docker.internal resolves inside Docker)
    ollama_api_base: str = 'http://host.docker.internal:11434'

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

    # Optional override for the provider_models.yaml catalog path
    model_catalog_path: str | None = None

    # SQLite database path for conversation persistence
    db_path: str = "spice_sibyl.db"

    # Telegram bot — leave empty to disable
    telegram_bot_token: str | None = None
    # Comma-separated Telegram user IDs allowed to use the bot (empty = everyone)
    telegram_allowed_users: str | None = None
    # Default model used by the Telegram bot (falls back to default_model)
    telegram_default_model: str | None = None

    # Logging level for the application (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    log_level: str = "INFO"

    # Master secret used to derive the Fernet encryption key for vaulted API keys.
    # Override with VAULT_SECRET_KEY env var in production.
    vault_secret_key: str = "change-me-in-production"

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')


@lru_cache
def get_settings() -> 'Settings':
    """Return the cached application settings singleton."""
    return Settings()


settings = get_settings()
