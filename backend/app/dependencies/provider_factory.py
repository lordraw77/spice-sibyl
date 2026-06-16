"""
FastAPI dependency that resolves the correct provider adapter from a model string.

Routing rules (evaluated in order):
  agent/<model>       → OrchestratorProvider (proxies the Multi-MCP sidecar)
  cloudflare/<model>  → CloudflareProvider  (direct Workers AI HTTP calls)
  openrouter/<model>  → OpenRouterProvider  (LiteLLM via OpenRouter)
  gemini/<model>      → GeminiProvider      (LiteLLM via Google Generative AI)
  *                   → LiteLLMProvider     (all other prefixes: ollama, groq, …)
"""
from app.providers.cerebras_provider import CerebrasProvider
from app.providers.cloudflare_provider import CloudflareProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.litellm_provider import LiteLLMProvider
from app.providers.mistral_provider import MistralProvider
from app.providers.nvidia_provider import NvidiaProvider
from app.providers.openrouter_provider import OpenRouterProvider
from app.providers.orchestrator_provider import OrchestratorProvider


def get_provider(model: str | None = None):
    """Return the provider instance that handles the given model identifier."""
    if model and model.startswith('agent/'):
        return OrchestratorProvider()
    if model and model.startswith('cloudflare/'):
        return CloudflareProvider()
    if model and model.startswith('openrouter/'):
        return OpenRouterProvider()
    if model and model.startswith('gemini/'):
        return GeminiProvider()
    if model and model.startswith('cerebras/'):
        return CerebrasProvider()
    if model and model.startswith('mistral/'):
        return MistralProvider()
    if model and model.startswith('nvidia/'):
        return NvidiaProvider()
    return LiteLLMProvider()
