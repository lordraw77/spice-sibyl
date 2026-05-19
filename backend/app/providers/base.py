"""
Abstract base class that every AI provider adapter must implement.

Concrete subclasses (LiteLLMProvider, CloudflareProvider, …) are selected at
request time by the provider factory based on the model prefix.
"""

from abc import ABC, abstractmethod

from app.schemas.chat import ChatCompletionRequest


class BaseProvider(ABC):
    @abstractmethod
    async def complete(self, request: ChatCompletionRequest):
        """Return a single, non-streaming chat completion response."""
        raise NotImplementedError

    @abstractmethod
    async def stream(self, request: ChatCompletionRequest):
        """Yield successive response chunks for a streaming chat completion."""
        raise NotImplementedError

    @abstractmethod
    async def list_models(self):
        """Return the list of models available through this provider."""
        raise NotImplementedError
