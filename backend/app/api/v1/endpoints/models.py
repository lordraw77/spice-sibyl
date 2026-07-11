"""
GET /v1/models — return all available models and a per-provider summary.

Both come from the discovered catalog (see app.data.model_catalog): models
are registered by running provider discovery, either manually from the
Discovery page or via the automatic startup refresh.

When an admin has curated a model allow-list on the Settings page
(owner_key='app:model_selection'), the returned list is filtered to it, so
every model dropdown in the UI only offers the selected models.
"""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from app.data.model_catalog import provider_summary_from_catalog
from app.db import settings_repository
from app.db.database import get_db
from app.dependencies.provider_factory import get_provider
from app.schemas.features import MODEL_SELECTION_OWNER_KEY, selected_model_ids

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get('')
@router.get('/')
async def list_models(db: aiosqlite.Connection = Depends(get_db)):
    """Return the model list (filtered by the admin allow-list) and a provider summary."""
    try:
        provider = get_provider()
        data = await provider.list_models()
        selection = selected_model_ids(
            await settings_repository.get(db, MODEL_SELECTION_OWNER_KEY)
        )
        if selection is not None:
            data = [m for m in data if m.get('id') in selection]
        return {
            'object': 'list',
            'data': data,
            'providers': provider_summary_from_catalog(),
        }
    except Exception as exc:
        logger.exception('Failed to list models')
        raise HTTPException(status_code=500, detail=str(exc)) from exc
