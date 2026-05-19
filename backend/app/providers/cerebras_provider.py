"""
Cerebras provider adapter — direct HTTP via httpx.

Calls https://api.cerebras.ai/v1/chat/completions directly (no LiteLLM, no SDK)
because the Cerebras REST API is OpenAI-compatible and includes a time_info block
with sub-millisecond timing that we surface in the gateway metrics.

The model string must carry the 'cerebras/' prefix so the dependency factory
routes here; the prefix is stripped before the request.
"""

import json
import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)
from app.data.model_catalog import get_model_metadata
from app.providers.base import BaseProvider
from app.schemas.chat import ChatCompletionRequest

_BASE_URL = 'https://api.cerebras.ai/v1'


def _require_key() -> str:
    if not settings.cerebras_api_key:
        raise ValueError('CEREBRAS_API_KEY is not configured in the backend.')
    return settings.cerebras_api_key


def _strip_prefix(model: str) -> str:
    return model.removeprefix('cerebras/')


def _headers(api_key: str) -> dict:
    return {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }


def _body(request: ChatCompletionRequest, stream: bool) -> dict:
    return {
        'model': _strip_prefix(request.model),
        'messages': [{'role': m.role, 'content': m.content} for m in request.messages],
        'stream': stream,
        'temperature': request.temperature if request.temperature is not None else 0.7,
        'max_tokens': request.max_tokens if request.max_tokens is not None else -1,
    }


def _metrics_from_time_info(time_info: dict | None, completion_tokens: int, wall_ms: int) -> dict:
    """Build the metrics dict preferring Cerebras time_info over wall-clock."""
    if time_info and time_info.get('total_time') is not None:
        latency_ms = int(time_info['total_time'] * 1000)
        prompt_time = time_info.get('prompt_time') or time_info['total_time']
        first_token_ms = int(prompt_time * 1000)
        completion_time_s = time_info.get('completion_time') or 0
    else:
        latency_ms = wall_ms
        first_token_ms = wall_ms
        completion_time_s = wall_ms / 1000

    tokens_per_second = (
        round(completion_tokens / completion_time_s, 2)
        if completion_time_s > 0 and completion_tokens > 0
        else None
    )
    return {
        'latency_ms': latency_ms,
        'first_token_ms': first_token_ms,
        'tokens_per_second': tokens_per_second,
    }


class CerebrasProvider(BaseProvider):

    async def complete(self, request: ChatCompletionRequest):
        api_key = _require_key()
        model_meta = get_model_metadata(request.model)
        started_at = time.perf_counter()
        payload = _body(request, stream=False)
        logger.debug('Cerebras complete → model=%s body=%s', payload['model'], payload)

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f'{_BASE_URL}/chat/completions',
                headers=_headers(api_key),
                json=payload,
            )
            logger.debug('Cerebras response status=%s body=%s', resp.status_code, resp.text[:500])
            resp.raise_for_status()
            data = resp.json()

        wall_ms = int((time.perf_counter() - started_at) * 1000)

        usage = data.get('usage') or {}
        completion_tokens = usage.get('completion_tokens') or 0
        prompt_tokens = usage.get('prompt_tokens') or 0
        total_tokens = usage.get('total_tokens') or 0

        m = _metrics_from_time_info(data.get('time_info'), completion_tokens, wall_ms)
        choice = (data.get('choices') or [{}])[0]
        message = choice.get('message') or {}
        finish_reason = choice.get('finish_reason') or 'stop'

        return {
            'id': data.get('id', f'cb-{int(time.time() * 1000)}'),
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
                        'provider': 'cerebras',
                        'latency_ms': m['latency_ms'],
                        'first_token_ms': m['first_token_ms'],
                        'prompt_tokens': prompt_tokens,
                        'completion_tokens': completion_tokens,
                        'total_tokens': total_tokens,
                        'tokens_per_second': m['tokens_per_second'],
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
                'latency_ms': m['latency_ms'],
                'first_token_ms': m['first_token_ms'],
                'tokens_per_second': m['tokens_per_second'],
                'provider': 'cerebras',
                'estimated_cost': None,
            },
        }

    async def stream(self, request: ChatCompletionRequest):
        api_key = _require_key()
        model_meta = get_model_metadata(request.model)
        started_at = time.perf_counter()

        total_content = ''
        finish_reason = 'stop'
        final_time_info = None
        final_usage = None
        response_id = f'cb-{int(time.time() * 1000)}'
        response_created = int(time.time())
        payload = _body(request, stream=True)
        logger.debug('Cerebras stream → model=%s', payload['model'])

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                'POST',
                f'{_BASE_URL}/chat/completions',
                headers={**_headers(api_key), 'Accept': 'text/event-stream'},
                json=payload,
            ) as resp:
                logger.debug('Cerebras stream status=%s', resp.status_code)
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

                    if chunk.get('time_info'):
                        final_time_info = chunk['time_info']
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

        wall_ms = int((time.perf_counter() - started_at) * 1000)
        completion_tokens = (final_usage or {}).get('completion_tokens') or 0
        prompt_tokens = (final_usage or {}).get('prompt_tokens') or 0
        total_tokens = (final_usage or {}).get('total_tokens') or 0

        m = _metrics_from_time_info(final_time_info, completion_tokens, wall_ms)

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
                        'provider': 'cerebras',
                        'latency_ms': m['latency_ms'],
                        'first_token_ms': m['first_token_ms'],
                        'prompt_tokens': prompt_tokens,
                        'completion_tokens': completion_tokens,
                        'total_tokens': total_tokens,
                        'tokens_per_second': m['tokens_per_second'],
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
                'latency_ms': m['latency_ms'],
                'first_token_ms': m['first_token_ms'],
                'tokens_per_second': m['tokens_per_second'],
                'provider': 'cerebras',
                'estimated_cost': None,
            },
        }

    async def list_models(self):
        return []
