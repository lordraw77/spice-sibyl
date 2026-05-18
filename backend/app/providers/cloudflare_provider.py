import json
import time
from typing import AsyncGenerator

import httpx

from app.core.config import settings
from app.data.model_catalog import get_model_metadata
from app.providers.base import BaseProvider
from app.schemas.chat import ChatCompletionRequest


class CloudflareProvider(BaseProvider):
    def _require_config(self):
        if not settings.cloudflare_account_id:
            raise ValueError('CLOUDFLARE_ACCOUNT_ID non configurato nel backend.')
        if not settings.cloudflare_api_key:
            raise ValueError('CLOUDFLARE_API_KEY non configurato nel backend.')

    def _extract_model_name(self, model: str) -> str:
        if model.startswith('cloudflare/'):
            return model.split('/', 1)[1]
        return model

    def _serialize_messages(self, messages) -> list[dict]:
        return [{'role': m.role, 'content': m.content} for m in messages]

    def _build_url(self, model: str) -> str:
        model_name = self._extract_model_name(model)
        return (
            f'https://api.cloudflare.com/client/v4/accounts/'
            f'{settings.cloudflare_account_id}/ai/run/{model_name}'
        )

    def _build_headers(self, stream: bool = False) -> dict[str, str]:
        headers = {
            'Authorization': f'Bearer {settings.cloudflare_api_key}',
            'Content-Type': 'application/json',
        }
        if stream:
            headers['Accept'] = 'text/event-stream'
        return headers

    def _extract_text(self, payload: dict) -> str:
        result = payload.get('result') or {}

        if isinstance(result, dict):
            choices = result.get('choices')
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, dict):
                    message = first.get('message') or {}
                    if isinstance(message, dict) and isinstance(message.get('content'), str):
                        return message['content']

                    delta = first.get('delta') or {}
                    if isinstance(delta, dict) and isinstance(delta.get('content'), str):
                        return delta['content']

            if isinstance(result.get('response'), str):
                return result['response']
            if isinstance(result.get('text'), str):
                return result['text']
            if isinstance(result.get('output_text'), str):
                return result['output_text']
            if isinstance(result.get('result'), str):
                return result['result']
            if isinstance(result.get('content'), str):
                return result['content']
            if isinstance(result.get('message'), str):
                return result['message']
            if isinstance(result.get('messages'), list):
                parts = []
                for msg in result['messages']:
                    if isinstance(msg, dict) and isinstance(msg.get('content'), str):
                        parts.append(msg['content'])
                if parts:
                    return '\n'.join(parts)

        if isinstance(result, str):
            return result

        raise ValueError(
            f'Risposta Cloudflare non riconosciuta: {json.dumps(payload)[:1200]}'
        )

    async def complete(self, request: ChatCompletionRequest):
        self._require_config()
        started_at = time.perf_counter()
        url = self._build_url(request.model)

        body = {
            'messages': self._serialize_messages(request.messages),
        }
        if request.max_tokens is not None:
            body['max_tokens'] = request.max_tokens

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                url,
                headers=self._build_headers(),
                json=body,
            )
            response.raise_for_status()
            payload = response.json()

        text = self._extract_text(payload)
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        model_meta = get_model_metadata(request.model)

        usage = None
        finish_reason = 'stop'
        result = payload.get('result') or {}
        if isinstance(result, dict):
            usage = result.get('usage')
            choices = result.get('choices')
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                finish_reason = choices[0].get('finish_reason') or 'stop'

        return {
            'id': f'cf-{int(time.time() * 1000)}',
            'object': 'chat.completion',
            'created': int(time.time()),
            'model': request.model,
            'choices': [
                {
                    'index': 0,
                    'finish_reason': finish_reason,
                    'message': {
                        'role': 'assistant',
                        'content': text,
                        'model': request.model,
                        'provider': 'cloudflare',
                        'latency_ms': latency_ms,
                        'first_token_ms': latency_ms,
                        'created_at': int(time.time()),
                        'capabilities': model_meta.get('capabilities', []),
                        'free': model_meta.get('free', False),
                    },
                }
            ],
            'usage': usage,
            'metrics': {
                'latency_ms': latency_ms,
                'first_token_ms': latency_ms,
                'tokens_per_second': None,
                'provider': 'cloudflare',
                'estimated_cost': None,
            },
        }

    async def stream(self, request: ChatCompletionRequest) -> AsyncGenerator[dict, None]:
        result = await self.complete(request)
        content = result['choices'][0]['message']['content']

        if content:
            yield {
                'id': result['id'],
                'object': 'chat.completion.chunk',
                'created': result['created'],
                'model': request.model,
                'choices': [
                    {
                        'index': 0,
                        'delta': {'content': content},
                        'finish_reason': None,
                    }
                ],
            }

        yield {
            'id': f'meta-{int(time.time())}',
            'object': 'chat.completion.meta',
            'created': int(time.time()),
            'model': request.model,
            'choices': [
                {
                    'index': 0,
                    'finish_reason': result['choices'][0]['finish_reason'],
                    'message': result['choices'][0]['message'],
                }
            ],
            'usage': result.get('usage'),
            'metrics': result.get('metrics'),
        }

    async def list_models(self):
        return []