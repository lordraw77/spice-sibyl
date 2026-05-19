"""
POST /v1/chat/completions — OpenAI-compatible chat completion endpoint.

Resolves the correct provider adapter from the model prefix, delegates the
request, and returns the full response.  Streaming is handled separately via
the ChatService SSE wrapper (not yet wired to this router).
"""

import logging

from fastapi import APIRouter, HTTPException

from app.dependencies.provider_factory import get_provider
from app.schemas.chat import ChatCompletionRequest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post('/completions')
async def chat_completions(payload: ChatCompletionRequest):
    """Handle a chat completion request and return the provider response."""
    provider = get_provider(payload.model)
    try:
        return await provider.complete(payload)
    except Exception as exc:
        logger.exception('Chat completion failed for model=%s', payload.model)
        raise HTTPException(
            status_code=500,
            detail={
                'message': str(exc),
                'model': payload.model,
            },
        ) from exc