"""
Legacy service-layer factory — kept for backward compatibility with ChatService.

New code should use app.dependencies.provider_factory.get_provider(), which
also routes cloudflare/ and openrouter/ prefixes to their dedicated adapters.
"""

from app.core.config import settings
from app.providers.litellm_provider import LiteLLMProvider
from app.providers.mock_provider import MockProvider


class ProviderFactory:
    @staticmethod
    def get_provider(model: str):
        """Return MockProvider when the model prefix is 'mock/' or the global override is set."""
        if model.startswith("mock/") or settings.litellm_provider == "mock":
            return MockProvider()
        return LiteLLMProvider()
