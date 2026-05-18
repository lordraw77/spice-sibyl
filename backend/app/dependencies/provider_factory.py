from functools import lru_cache

from app.core.config import settings
from app.providers.cloudflare_provider import CloudflareProvider
from app.providers.litellm_provider import LiteLLMProvider
from app.providers.openrouter_provider import OpenRouterProvider


def get_provider(model: str | None = None):
    if model and model.startswith('cloudflare/'):
        return CloudflareProvider()
    if model and model.startswith('openrouter/'):
        return OpenRouterProvider()
    return LiteLLMProvider()
