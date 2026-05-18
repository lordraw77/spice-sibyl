import logging

from fastapi import APIRouter, HTTPException

from app.dependencies.provider_factory import get_provider
from app.schemas.chat import ChatCompletionRequest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post('/completions')
async def chat_completions(payload: ChatCompletionRequest):
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