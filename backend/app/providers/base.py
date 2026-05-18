from abc import ABC, abstractmethod

from app.schemas.chat import ChatCompletionRequest


class BaseProvider(ABC):
    @abstractmethod
    async def complete(self, request: ChatCompletionRequest):
        raise NotImplementedError

    @abstractmethod
    async def stream(self, request: ChatCompletionRequest):
        raise NotImplementedError

    @abstractmethod
    async def list_models(self):
        raise NotImplementedError
