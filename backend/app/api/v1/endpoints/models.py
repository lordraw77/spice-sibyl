"""
GET /v1/models — return all available models and a per-provider summary.

The response merges:
  - dynamic models discovered at runtime (e.g. Ollama /api/tags)
  - static models declared in provider_models.yaml
  - a deduplicated provider summary with aggregate capability lists
"""

import logging

from fastapi import APIRouter, HTTPException

from app.data.model_catalog import merge_provider_summary
from app.dependencies.provider_factory import get_provider

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get('')
@router.get('/')
async def list_models():
    """Return the full model list and a provider-level summary."""
    try:
        provider = get_provider()
        data = await provider.list_models()
        return {
            'object': 'list',
            'data': data,
            'providers': merge_provider_summary(data),
        }
    except Exception as exc:
        logger.exception('Failed to list models')
        raise HTTPException(status_code=500, detail=str(exc)) from exc
