"""
Service-layer provider factory — delegates to the canonical dependency factory.

ChatService uses this module; keeping it as a thin wrapper ensures a single
routing table in app.dependencies.provider_factory.
"""

from app.core.config import settings
from app.dependencies.provider_factory import get_provider
from app.providers.mock_provider import MockProvider


class ProviderFactory:
    @staticmethod
    def get_provider(model: str):
        if model.startswith('mock/') or settings.litellm_provider == 'mock':
            return MockProvider()
        return get_provider(model)
