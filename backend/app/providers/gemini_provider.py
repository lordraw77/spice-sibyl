"""
Google Gemini provider adapter — routes chat completions through LiteLLM.

The model string must carry the 'gemini/' prefix so the dependency factory
directs the request here.  LiteLLM handles the Google Generative AI API
translation; this adapter injects the API key from settings and attaches
gateway metrics to every response.

list_models() returns an empty list because the model catalog is defined
statically in provider_models.yaml.
"""

import time

from litellm import acompletion

from app.data.model_catalog import get_model_metadata
from app.providers.base import BaseProvider
from app.schemas.chat import ChatCompletionRequest
from app.services import key_resolver


class GeminiProvider(BaseProvider):
    def _build_call_kwargs(self, request: ChatCompletionRequest) -> dict:
        api_key = key_resolver.resolve('gemini')
        if not api_key:
            raise ValueError('GEMINI_API_KEY is not configured in the backend.')

        return {
            'model': request.model,
            'messages': self._serialize_messages(request.messages),
            'max_tokens': request.max_tokens,
            'temperature': request.temperature if request.temperature is not None else 0.7,
            'api_key': api_key,
        }

    def _serialize_messages(self, messages) -> list[dict]:
        """Convert Pydantic message objects to plain dicts for LiteLLM."""
        return [{'role': m.role, 'content': m.content} for m in messages]

    async def complete(self, request: ChatCompletionRequest):
        """Execute a non-streaming completion and attach gateway metrics."""
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
        model_meta = get_model_metadata(request.model)

        payload['metrics'] = {
            'latency_ms': latency_ms,
            'first_token_ms': latency_ms,
            'tokens_per_second': tokens_per_second,
            'provider': 'gemini',
            'estimated_cost': None,
        }

        if payload.get('choices'):
            for choice in payload['choices']:
                msg = choice.get('message') or {}
                msg['provider'] = 'gemini'
                msg['latency_ms'] = latency_ms
                msg['capabilities'] = model_meta.get('capabilities', [])
                msg['free'] = model_meta.get('free', False)

        return payload

    async def stream(self, request: ChatCompletionRequest):
        """Yield raw LiteLLM chunk dicts for SSE streaming."""
        kwargs = self._build_call_kwargs(request)
        kwargs['stream'] = True
        response = await acompletion(**kwargs)
        async for chunk in response:
            yield chunk.model_dump()

    async def list_models(self):
        """Model catalog is defined statically in provider_models.yaml."""
        return []
