import time

from litellm import acompletion

from app.core.config import settings
from app.providers.base import BaseProvider
from app.schemas.chat import ChatCompletionRequest


class OpenRouterProvider(BaseProvider):
    def _build_call_kwargs(self, request: ChatCompletionRequest) -> dict:
        if not settings.openrouter_api_key:
            raise ValueError('OPENROUTER_API_KEY non configurata nel backend.')

        model = request.model

        return {
            'model': model,
            'messages': self._serialize_messages(request.messages),
            'max_tokens': request.max_tokens,
            'temperature': request.temperature if request.temperature is not None else 0.7,
            'api_key': settings.openrouter_api_key,
        }

    def _serialize_messages(self, messages) -> list[dict]:
        return [{'role': m.role, 'content': m.content} for m in messages]

    async def complete(self, request: ChatCompletionRequest):
        started_at = time.perf_counter()
        response = await acompletion(**self._build_call_kwargs(request))
        payload = response.model_dump()

        latency_ms = int((time.perf_counter() - started_at) * 1000)
        usage = payload.get('usage') or {}
        completion_tokens = usage.get('completion_tokens') or 0
        tokens_per_second = (
            round(completion_tokens / (latency_ms / 1000), 2)
            if latency_ms > 0 and completion_tokens > 0
            else None
        )

        payload['metrics'] = {
            'latency_ms': latency_ms,
            'first_token_ms': latency_ms,
            'tokens_per_second': tokens_per_second,
            'provider': 'openrouter',
            'estimated_cost': None,
        }

        return payload

    async def stream(self, request: ChatCompletionRequest):
        kwargs = self._build_call_kwargs(request)
        kwargs['stream'] = True
        response = await acompletion(**kwargs)
        async for chunk in response:
            yield chunk.model_dump()
            
    async def list_models(self):
        return []