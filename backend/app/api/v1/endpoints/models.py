"""
GET /v1/models — return all available models and a per-provider summary.

Both come from the discovered catalog (see app.data.model_catalog): models
are registered by running provider discovery, either manually from the
Discovery page or via the automatic startup refresh.
"""

import logging

from fastapi import APIRouter, HTTPException

from app.data.model_catalog import provider_summary_from_catalog
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
            'providers': provider_summary_from_catalog(),
        }
    except Exception as exc:
        logger.exception('Failed to list models')
        raise HTTPException(status_code=500, detail=str(exc)) from exc
