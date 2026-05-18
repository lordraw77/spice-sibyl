import os
import logging
import time

import httpx
from litellm import acompletion

from app.core.config import settings
from app.data.model_catalog import get_model_metadata, iter_configured_models
from app.providers.base import BaseProvider
from app.schemas.chat import ChatCompletionRequest

logger = logging.getLogger(__name__)

if settings.cloudflare_account_id:
    os.environ.setdefault('CLOUDFLARE_ACCOUNT_ID', settings.cloudflare_account_id)
if settings.cloudflare_api_key:
    os.environ.setdefault('CLOUDFLARE_API_KEY', settings.cloudflare_api_key)


class LiteLLMProvider(BaseProvider):
    def _resolve_api_key(self, model: str) -> str | None:
        if model.startswith('groq/'):
            return settings.groq_api_key
        if model.startswith('openrouter/'):
            return settings.openrouter_api_key
        if model.startswith('gemini/'):
            return settings.gemini_api_key
        if model.startswith('together_ai/'):
            return settings.together_api_key
        if model.startswith('fireworks_ai/'):
            return settings.fireworks_api_key
        if model.startswith('mistral/'):
            return settings.mistral_api_key
        if model.startswith('huggingface/'):
            return settings.hf_token
        if model.startswith('ollama/'):
            return None
        return settings.openai_api_key

    def _serialize_messages(self, messages) -> list[dict]:
        return [{'role': m.role, 'content': m.content} for m in messages]

    def _build_call_kwargs(self, request: ChatCompletionRequest) -> dict:
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

        return kwargs

    async def complete(self, request: ChatCompletionRequest):
        started_at = time.perf_counter()
        kwargs = self._build_call_kwargs(request)

        logger.warning(
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

        hidden = getattr(response, '_hidden_params', {}) or {}

        payload['metrics'] = {
            'latency_ms': latency_ms,
            'first_token_ms': latency_ms,
            'tokens_per_second': tokens_per_second,
            'provider': request.model.split('/', 1)[0] if '/' in request.model else 'unknown',
            'estimated_cost': hidden.get('response_cost'),
        }
        return payload

    async def stream(self, request: ChatCompletionRequest):
        started_at = time.perf_counter()
        first_token_ms = None
        final_usage: dict = {}
        final_finish_reason = None
        final_content_parts: list[str] = []

        kwargs = self._build_call_kwargs(request)
        kwargs['stream'] = True
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
        models = []
        url = f"{settings.ollama_api_base.rstrip('/')}/api/tags"
        logger.warning('Fetching Ollama models from %s', url)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                payload = resp.json()

            logger.warning('Ollama /api/tags payload: %s', payload)

            for item in payload.get('models', []):
                model_name = item.get('model') or item.get('name')
                if model_name:
                    capabilities = ['chat']
                    if 'coder' in model_name.lower():
                        capabilities.append('code')

                    models.append(
                        {
                            'id': f'ollama/{model_name}',
                            'object': 'model',
                            'owned_by': 'ollama',
                            'label': model_name,
                            'provider': 'ollama',
                            'enabled': True,
                            'configured': True,
                            'default': model_name == settings.default_model.removeprefix('ollama/'),
                            'free': True,
                            'capabilities': capabilities,
                        }
                    )
        except Exception as exc:
            logger.exception('Unable to load Ollama models: %s', exc)

        models.extend(iter_configured_models())

        seen: set = set()
        unique_models = []
        for m in models:
            if m['id'] not in seen:
                seen.add(m['id'])
                unique_models.append(m)

        return unique_models