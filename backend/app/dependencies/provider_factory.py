"""
FastAPI dependency that resolves the correct provider adapter from a model string.

Routing rules (evaluated in order):
  cloudflare/<model>  → CloudflareProvider  (direct Workers AI HTTP calls)
  openrouter/<model>  → OpenRouterProvider  (LiteLLM via OpenRouter)
  *                   → LiteLLMProvider     (all other prefixes: ollama, groq, gemini, …)
"""
from app.providers.cloudflare_provider import CloudflareProvider
from app.providers.litellm_provider import LiteLLMProvider
from app.providers.openrouter_provider import OpenRouterProvider


def get_provider(model: str | None = None):
    """Return the provider instance that handles the given model identifier."""
    if model and model.startswith('cloudflare/'):
        return CloudflareProvider()
    if model and model.startswith('openrouter/'):
        return OpenRouterProvider()
    return LiteLLMProvider()
