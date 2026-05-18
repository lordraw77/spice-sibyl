from functools import lru_cache

from app.core.config import settings
from app.providers.litellm_provider import LiteLLMProvider


@lru_cache
def get_provider():
    if settings.litellm_provider == 'litellm':
        return LiteLLMProvider()
    return LiteLLMProvider()
