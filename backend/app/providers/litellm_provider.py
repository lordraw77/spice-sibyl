"""
LiteLLM provider adapter — routes requests to any LiteLLM-supported backend.

Supported model prefixes and their mapped providers:
  ollama/       — local Ollama instance (api_base from settings)
  groq/         — Groq Cloud
  gemini/       — Google Gemini
  together_ai/  — Together AI
  fireworks_ai/ — Fireworks AI
  mistral/      — Mistral AI
  huggingface/  — HuggingFace Inference API
  openrouter/   — OpenRouter (fallback via LiteLLM; prefer OpenRouterProvider)
  *             — OpenAI (default)

Cloudflare env vars are forwarded to the process environment so LiteLLM can
pick them up when a cloudflare/ model is used through this adapter.
"""

import os
import logging
import time

from litellm import acompletion

from app.core.config import settings
from app.data.model_catalog import get_model_metadata, iter_configured_models
from app.providers.base import BaseProvider
from app.schemas.chat import ChatCompletionRequest
from app.services import key_resolver

logger = logging.getLogger(__name__)

# LiteLLM reads Cloudflare credentials from the environment, not from kwargs
if settings.cloudflare_account_id:
    os.environ.setdefault('CLOUDFLARE_ACCOUNT_ID', settings.cloudflare_account_id)
if settings.cloudflare_api_key:
    os.environ.setdefault('CLOUDFLARE_API_KEY', settings.cloudflare_api_key)

# Map model prefix → provider_id used by key_resolver
_PREFIX_TO_PROVIDER = {
    'groq/':         'groq',
    'openrouter/':   'openrouter',
    'gemini/':       'gemini',
    'together_ai/':  'together_ai',
    'fireworks_ai/': 'fireworks_ai',
    'mistral/':      'mistral',
    'huggingface/':  'huggingface',
    'openai/':       'openai',
}


class LiteLLMProvider(BaseProvider):
    def _resolve_api_key(self, model: str) -> str | None:
        """Return the API key for the provider inferred from the model prefix.
        Vault takes priority over environment variables."""
        if model.startswith('ollama/'):
            return None
        for prefix, provider_id in _PREFIX_TO_PROVIDER.items():
            if model.startswith(prefix):
                return key_resolver.resolve(provider_id)
        return key_resolver.resolve('openai')

    def _serialize_messages(self, messages) -> list[dict]:
        """Convert Pydantic message objects to plain dicts for LiteLLM."""
        result = []
        for m in messages:
            msg: dict = {'role': m.role}
            if m.content is not None:
                msg['content'] = m.content
            if m.tool_calls:
                msg['tool_calls'] = [tc.model_dump() for tc in m.tool_calls]
            if m.tool_call_id:
                msg['tool_call_id'] = m.tool_call_id
            if m.name:
                msg['name'] = m.name
            result.append(msg)
        return result

    def _build_call_kwargs(self, request: ChatCompletionRequest) -> dict:
        """Assemble the keyword arguments dict passed to litellm.acompletion."""
        model = request.model

        kwargs: dict = {
            'model': model,
            'messages': self._serialize_messages(request.messages),
            'max_tokens': request.max_tokens,
        }

        api_key = self._resolve_api_key(model)
        if api_key is not None:
            kwargs['api_key'] = api_key

        if model.startswith('ollama/'):
            kwargs['api_base'] = settings.ollama_api_base

        if request.temperature is not None:
            kwargs['temperature'] = request.temperature

        if request.tools:
            kwargs['tools'] = [t.model_dump() for t in request.tools]
            kwargs['tool_choice'] = request.tool_choice or 'auto'

        return kwargs

    async def complete(self, request: ChatCompletionRequest):
        """Execute a non-streaming completion and attach gateway metrics to the payload."""
        started_at = time.perf_counter()
        kwargs = self._build_call_kwargs(request)

        logger.debug(
            'Calling LiteLLM model=%s api_base=%s temperature=%s',
            kwargs['model'],
            kwargs.get('api_base'),
            kwargs.get('temperature'),
        )

        response = await acompletion(**kwargs)
        payload = response.model_dump()

        latency_ms = int((time.perf_counter() - started_at) * 1000)
        usage = payload.get('usage') or {}
        completion_tokens = usage.get('completion_tokens') or 0
        tokens_per_second = (
            round(completion_tokens / (latency_ms / 1000), 2)
            if latency_ms > 0 and completion_tokens > 0
            else None
        )

        # LiteLLM exposes cost data in _hidden_params, not in the standard payload
        hidden = getattr(response, '_hidden_params', {}) or {}

        payload['metrics'] = {
            'latency_ms': latency_ms,
            # first_token_ms is not meaningful for non-streaming calls
            'first_token_ms': None,
            'tokens_per_second': tokens_per_second,
            'provider': request.model.split('/', 1)[0] if '/' in request.model else 'unknown',
            'estimated_cost': hidden.get('response_cost'),
        }
        return payload

    async def stream(self, request: ChatCompletionRequest):
        """
        Yield SSE-compatible chunks followed by a final 'chat.completion.meta' object.

        The meta object carries aggregated telemetry (latency, token counts, cost)
        so the frontend can display performance stats after the stream ends.
        """
        started_at = time.perf_counter()
        first_token_ms = None
        final_usage: dict = {}
        final_finish_reason = None
        final_content_parts: list[str] = []

        kwargs = self._build_call_kwargs(request)
        kwargs['stream'] = True
        # Request usage stats in the final chunk so we can compute tokens/s
        kwargs['stream_options'] = {'include_usage': True}

        response = await acompletion(**kwargs)

        async for chunk in response:
            data = chunk.model_dump()
            choices = data.get('choices') or []
            if choices:
                delta = choices[0].get('delta') or {}
                content = delta.get('content')
                if content:
                    final_content_parts.append(content)
                    if first_token_ms is None:
                        # Record time-to-first-token on the first non-empty delta
                        first_token_ms = int((time.perf_counter() - started_at) * 1000)
                if choices[0].get('finish_reason'):
                    final_finish_reason = choices[0]['finish_reason']
            if data.get('usage'):
                final_usage = data['usage']
            yield data

        latency_ms = int((time.perf_counter() - started_at) * 1000)
        completion_tokens = final_usage.get('completion_tokens') or 0
        tokens_per_second = (
            round(completion_tokens / (latency_ms / 1000), 2)
            if latency_ms > 0 and completion_tokens > 0
            else None
        )
        model_meta = get_model_metadata(request.model)

        # Emit a non-standard 'chat.completion.meta' event as the final chunk
        yield {
            'id': f'meta-{int(time.time())}',
            'object': 'chat.completion.meta',
            'created': int(time.time()),
            'model': request.model,
            'choices': [
                {
                    'index': 0,
                    'finish_reason': final_finish_reason,
                    'message': {
                        'role': 'assistant',
                        'content': ''.join(final_content_parts),
                        'model': request.model,
                        'provider': model_meta.get('provider'),
                        'latency_ms': latency_ms,
                        'first_token_ms': first_token_ms,
                        'prompt_tokens': final_usage.get('prompt_tokens'),
                        'completion_tokens': final_usage.get('completion_tokens'),
                        'total_tokens': final_usage.get('total_tokens'),
                        'tokens_per_second': tokens_per_second,
                        'finish_reason': final_finish_reason,
                        'created_at': int(time.time()),
                        'capabilities': model_meta.get('capabilities', []),
                        'free': model_meta.get('free', False),
                    },
                }
            ],
            'usage': final_usage,
            'metrics': {
                'latency_ms': latency_ms,
                'first_token_ms': first_token_ms,
                'tokens_per_second': tokens_per_second,
                'provider': model_meta.get('provider'),
                'estimated_cost': None,
            },
        }

    async def list_models(self):
        """Return the model list from the discovered catalog (no live provider calls).

        Ollama models are part of the catalog like every other provider's:
        they appear via POST /providers/ollama/discover or the periodic
        startup refresh, keeping this call fast and deterministic even when
        a provider is unreachable.
        """
        return iter_configured_models()
