"""
POST /v1/chat/completions — OpenAI-compatible chat completion endpoint.

When stream=true the request is delegated to ChatService which returns an
SSE EventSourceResponse; otherwise the provider's complete() is called directly.
"""

import logging

from fastapi import APIRouter, HTTPException

from app.dependencies.provider_factory import get_provider
from app.schemas.chat import ChatCompletionRequest
from app.services.chat_service import ChatService

router = APIRouter()
logger = logging.getLogger(__name__)
_chat_service = ChatService()


@router.post('/completions')
async def chat_completions(payload: ChatCompletionRequest):
    """Handle a chat completion request; stream=true returns an SSE response."""
    if payload.stream:
        try:
            return await _chat_service.stream(payload)
        except Exception as exc:
            logger.exception('Streaming failed for model=%s', payload.model)
            raise HTTPException(
                status_code=500,
                detail={'message': str(exc), 'model': payload.model},
            ) from exc

    provider = get_provider(payload.model)
    try:
        return await provider.complete(payload)
    except Exception as exc:
        logger.exception('Chat completion failed for model=%s', payload.model)
        raise HTTPException(
            status_code=500,
            detail={'message': str(exc), 'model': payload.model},
        ) from exc