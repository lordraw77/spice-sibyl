from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SpiceSibyl API"
    app_env: str = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    api_key: str = "change-me"
    cors_origins: str = "http://localhost:4200,http://127.0.0.1:4200"
    default_model: str = "ollama/qwen2.5:7b-instruct"
    litellm_provider: str = "litellm"
    ollama_api_base: str = "http://host.docker.internal:11434"
    openai_api_key: str = "dummy"
    groq_api_key: str | None = None
    openrouter_api_key: str | None = None
    gemini_api_key: str | None = None
    cloudflare_api_key: str | None = None
    cloudflare_account_id: str | None = None
    together_api_key: str | None = None
    fireworks_api_key: str | None = None
    mistral_api_key: str | None = None
    hf_token: str | None = None
    model_catalog_path: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
