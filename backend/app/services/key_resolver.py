"""
key_resolver — single source of truth for API key lookup.

Priority: vault (DB, encrypted) > environment variable / settings.
Providers call resolve(provider_id) instead of reading settings directly so that
keys set via PUT /providers/{id}/key are actually used at request time.
"""

from app.core.config import settings
from app.services import vault_service

_PLACEHOLDER = frozenset({"dummy", "change-me", ""})


def _from_settings(provider_id: str) -> str | None:
    mapping: dict[str, str | None] = {
        "groq":         settings.groq_api_key,
        "openrouter":   settings.openrouter_api_key,
        "gemini":       settings.gemini_api_key,
        "cloudflare":   settings.cloudflare_api_key,
        "together_ai":  settings.together_api_key,
        "fireworks_ai": settings.fireworks_api_key,
        "mistral":      settings.mistral_api_key,
        "huggingface":  settings.hf_token,
        "openai":       settings.openai_api_key,
        "cerebras":     settings.cerebras_api_key,
        "nvidia":       settings.nvidia_api_key,
    }
    val = mapping.get(provider_id)
    return val if val and val not in _PLACEHOLDER else None


def resolve(provider_id: str) -> str | None:
    """Return the best available API key for provider_id, or None."""
    return vault_service.get(provider_id) or _from_settings(provider_id)


def is_configured(provider_id: str) -> bool:
    if provider_id in ("ollama", "mock"):
        return True
    if provider_id == "agent":
        # keyless: configured when the Multi-MCP sidecar URL is set
        return bool(settings.orchestrator_base_url)
    if provider_id == "cloudflare":
        # needs both key and account_id
        key = resolve("cloudflare")
        return bool(key and settings.cloudflare_account_id)
    return bool(resolve(provider_id))
