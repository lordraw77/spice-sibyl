"""
ChatService — thin orchestration layer between the API endpoints and providers.

Handles both non-streaming (complete) and SSE-streaming (stream) paths.
The "[DONE]" sentinel at the end of the stream follows the OpenAI SSE convention
so existing client libraries work without modification.
"""

import json
from sse_starlette.sse import EventSourceResponse

from app.schemas.chat import ChatCompletionRequest
from app.services.provider_factory import ProviderFactory


class ChatService:
    async def complete(self, request: ChatCompletionRequest):
        """Delegate to the appropriate provider and return the full response."""
        provider = ProviderFactory.get_provider(request.model)
        return await provider.complete(request)

    async def stream(self, request: ChatCompletionRequest):
        """Return an SSE response that yields chunks as they arrive from the provider."""
        provider = ProviderFactory.get_provider(request.model)

        async def event_generator():
            try:
                async for chunk in provider.stream(request):
                    yield {"event": "message", "data": json.dumps(chunk)}
                yield {"event": "done", "data": "[DONE]"}
            except Exception as exc:
                yield {"event": "error", "data": json.dumps({"message": str(exc)})}

        return EventSourceResponse(event_generator())

    async def list_models(self, model: str):
        """Return models available through the provider associated with the given model string."""
        provider = ProviderFactory.get_provider(model)
        return await provider.list_models()
