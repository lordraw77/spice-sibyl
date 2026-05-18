from app.core.config import settings
from app.providers.litellm_provider import LiteLLMProvider
from app.providers.mock_provider import MockProvider


class ProviderFactory:
    @staticmethod
    def get_provider(model: str):
        if model.startswith("mock/") or settings.litellm_provider == "mock":
            return MockProvider()
        return LiteLLMProvider()
