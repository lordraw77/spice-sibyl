import json
from sse_starlette.sse import EventSourceResponse

from app.schemas.chat import ChatCompletionRequest
from app.services.provider_factory import ProviderFactory


class ChatService:
    async def complete(self, request: ChatCompletionRequest):
        provider = ProviderFactory.get_provider(request.model)
        return await provider.complete(request)

    async def stream(self, request: ChatCompletionRequest):
        provider = ProviderFactory.get_provider(request.model)

        async def event_generator():
            async for chunk in provider.stream(request):
                yield {"event": "message", "data": json.dumps(chunk)}
            yield {"event": "done", "data": "[DONE]"}

        return EventSourceResponse(event_generator())

    async def list_models(self, model: str):
        provider = ProviderFactory.get_provider(model)
        return await provider.list_models()
