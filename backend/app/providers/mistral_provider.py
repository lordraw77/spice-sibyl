"""
Mistral AI provider adapter — direct HTTP via httpx.

Calls https://api.mistral.ai/v1/chat/completions directly (no LiteLLM).
The Mistral API is OpenAI-compatible; streaming uses standard SSE.

The model string must carry the 'mistral/' prefix so the dependency factory
routes here; the prefix is stripped before the request.
"""

import json
import logging
import time

import httpx

from app.core.config import settings
from app.data.model_catalog import get_model_metadata
from app.providers.base import BaseProvider
from app.schemas.chat import ChatCompletionRequest

logger = logging.getLogger(__name__)

_BASE_URL = 'https://api.mistral.ai/v1'


def _require_key() -> str:
    if not settings.mistral_api_key:
        raise ValueError('MISTRAL_API_KEY is not configured in the backend.')
    return settings.mistral_api_key


def _strip_prefix(model: str) -> str:
    return model.removeprefix('mistral/')


def _headers(api_key: str) -> dict:
    return {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }


def _body(request: ChatCompletionRequest, stream: bool) -> dict:
    body: dict = {
        'model': _strip_prefix(request.model),
        'messages': [{'role': m.role, 'content': m.content} for m in request.messages],
        'stream': stream,
        'temperature': request.temperature if request.temperature is not None else 0.7,
    }
    if request.max_tokens is not None:
        body['max_tokens'] = request.max_tokens
    return body


class MistralProvider(BaseProvider):

    async def complete(self, request: ChatCompletionRequest):
        api_key = _require_key()
        model_meta = get_model_metadata(request.model)
        started_at = time.perf_counter()
        payload = _body(request, stream=False)
        logger.debug('Mistral complete → model=%s', payload['model'])

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f'{_BASE_URL}/chat/completions',
                headers=_headers(api_key),
                json=payload,
            )
            logger.debug('Mistral response status=%s body=%s', resp.status_code, resp.text[:500])
            resp.raise_for_status()
            data = resp.json()

        latency_ms = int((time.perf_counter() - started_at) * 1000)

        usage = data.get('usage') or {}
        completion_tokens = usage.get('completion_tokens') or 0
        prompt_tokens = usage.get('prompt_tokens') or 0
        total_tokens = usage.get('total_tokens') or 0
        tokens_per_second = (
            round(completion_tokens / (latency_ms / 1000), 2)
            if latency_ms > 0 and completion_tokens > 0
            else None
        )

        choice = (data.get('choices') or [{}])[0]
        message = choice.get('message') or {}
        finish_reason = choice.get('finish_reason') or 'stop'

        return {
            'id': data.get('id', f'mistral-{int(time.time() * 1000)}'),
            'object': 'chat.completion',
            'created': data.get('created', int(time.time())),
            'model': request.model,
            'choices': [
                {
                    'index': 0,
                    'finish_reason': finish_reason,
                    'message': {
                        'role': message.get('role', 'assistant'),
                        'content': message.get('content', ''),
                        'model': request.model,
                        'provider': 'mistral',
                        'latency_ms': latency_ms,
                        'first_token_ms': latency_ms,
                        'prompt_tokens': prompt_tokens,
                        'completion_tokens': completion_tokens,
                        'total_tokens': total_tokens,
                        'tokens_per_second': tokens_per_second,
                        'finish_reason': finish_reason,
                        'created_at': data.get('created', int(time.time())),
                        'capabilities': model_meta.get('capabilities', []),
                        'free': model_meta.get('free', False),
                    },
                }
            ],
            'usage': {
                'prompt_tokens': prompt_tokens,
                'completion_tokens': completion_tokens,
                'total_tokens': total_tokens,
            },
            'metrics': {
                'latency_ms': latency_ms,
                'first_token_ms': latency_ms,
                'tokens_per_second': tokens_per_second,
                'provider': 'mistral',
                'estimated_cost': None,
            },
        }

    async def stream(self, request: ChatCompletionRequest):
        api_key = _require_key()
        model_meta = get_model_metadata(request.model)
        started_at = time.perf_counter()
        payload = _body(request, stream=True)
        logger.debug('Mistral stream → model=%s', payload['model'])

        total_content = ''
        finish_reason = 'stop'
        final_usage = None
        response_id = f'mistral-{int(time.time() * 1000)}'
        response_created = int(time.time())

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                'POST',
                f'{_BASE_URL}/chat/completions',
                headers={**_headers(api_key), 'Accept': 'text/event-stream'},
                json=payload,
            ) as resp:
                logger.debug('Mistral stream status=%s', resp.status_code)
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith('data:'):
                        continue
                    raw = line[5:].strip()
                    if raw == '[DONE]':
                        break
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    response_id = chunk.get('id', response_id)
                    response_created = chunk.get('created', response_created)

                    if chunk.get('usage'):
                        final_usage = chunk['usage']

                    choices = chunk.get('choices') or []
                    if not choices:
                        continue

                    choice = choices[0]
                    if choice.get('finish_reason'):
                        finish_reason = choice['finish_reason']

                    content = (choice.get('delta') or {}).get('content') or ''
                    if not content:
                        continue

                    total_content += content
                    yield {
                        'id': response_id,
                        'object': 'chat.completion.chunk',
                        'created': response_created,
                        'model': request.model,
                        'choices': [
                            {
                                'index': 0,
                                'delta': {'content': content},
                                'finish_reason': None,
                            }
                        ],
                    }

        latency_ms = int((time.perf_counter() - started_at) * 1000)
        completion_tokens = (final_usage or {}).get('completion_tokens') or 0
        prompt_tokens = (final_usage or {}).get('prompt_tokens') or 0
        total_tokens = (final_usage or {}).get('total_tokens') or 0
        tokens_per_second = (
            round(completion_tokens / (latency_ms / 1000), 2)
            if latency_ms > 0 and completion_tokens > 0
            else None
        )

        yield {
            'id': f'meta-{response_id}',
            'object': 'chat.completion.meta',
            'created': response_created,
            'model': request.model,
            'choices': [
                {
                    'index': 0,
                    'finish_reason': finish_reason,
                    'message': {
                        'role': 'assistant',
                        'content': total_content,
                        'model': request.model,
                        'provider': 'mistral',
                        'latency_ms': latency_ms,
                        'first_token_ms': latency_ms,
                        'prompt_tokens': prompt_tokens,
                        'completion_tokens': completion_tokens,
                        'total_tokens': total_tokens,
                        'tokens_per_second': tokens_per_second,
                        'finish_reason': finish_reason,
                        'created_at': response_created,
                        'capabilities': model_meta.get('capabilities', []),
                        'free': model_meta.get('free', False),
                    },
                }
            ],
            'usage': {
                'prompt_tokens': prompt_tokens,
                'completion_tokens': completion_tokens,
                'total_tokens': total_tokens,
            },
            'metrics': {
                'latency_ms': latency_ms,
                'first_token_ms': latency_ms,
                'tokens_per_second': tokens_per_second,
                'provider': 'mistral',
                'estimated_cost': None,
            },
        }

    async def list_models(self):
        return []
