"""
GET    /v1/providers               — list all providers with live configuration status
POST   /v1/providers/{id}/test     — test connectivity to a provider
POST   /v1/providers/{id}/discover — fetch the provider's live model catalog and persist it
PATCH  /v1/providers/{id}          — enable or disable a provider
PUT    /v1/providers/{id}/key      — encrypt and vault an API key for a provider
DELETE /v1/providers/{id}/key      — remove a vaulted key
"""

import time
import logging

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import settings
from app.data import discovered_catalog
from app.data.model_catalog import provider_summary_from_catalog
from app.data.runtime_config import get_provider_override, set_provider_override
from app.services.model_discovery import DiscoveryError
from app.db import audit_repository
from app.db.database import get_db
from app.db import vault_repository
from app.dependencies.auth import get_current_user
from app.providers.registry import PROVIDERS, get_descriptor
from app.schemas.auth import UserOut
from app.services import key_resolver
from app.schemas.providers import (
    ProviderKeyRequest,
    ProviderStatus,
    ProviderTestResult,
    ProviderUpdateRequest,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _is_enabled(provider_id: str, catalog_enabled: bool) -> bool:
    override = get_provider_override(provider_id)
    return override.get('enabled', catalog_enabled)


def _build_status(entry: dict, pid: str) -> ProviderStatus:
    descriptor = get_descriptor(pid)
    return ProviderStatus(
        id=pid,
        label=descriptor.label if descriptor else entry['label'],
        enabled=_is_enabled(pid, entry.get('enabled', True)),
        configured=key_resolver.is_configured(pid),
        key_hint=descriptor.key_hint if descriptor else None,
        model_count=entry['model_count'],
        capabilities=entry['capabilities'],
        docs_url=descriptor.docs_url if descriptor else None,
    )


@router.get('')
async def list_providers() -> list[ProviderStatus]:
    catalog = provider_summary_from_catalog()
    return [_build_status(entry, entry['id']) for entry in catalog]


@router.patch('/{provider_id}')
async def update_provider(provider_id: str, body: ProviderUpdateRequest) -> ProviderStatus:
    if provider_id not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    overrides = {k: v for k, v in (('enabled', body.enabled), ('default_model', body.default_model)) if v is not None}
    if overrides:
        set_provider_override(provider_id, **overrides)
    entry = next(e for e in provider_summary_from_catalog() if e['id'] == provider_id)
    return _build_status(entry, provider_id)


@router.put('/{provider_id}/key')
async def set_provider_key(
    provider_id: str,
    body: ProviderKeyRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
) -> dict:
    """Encrypt and store the API key in the vault (SQLite, AES via Fernet)."""
    if provider_id not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    if not body.api_key or body.api_key in {'dummy', 'change-me', ''}:
        raise HTTPException(status_code=422, detail="api_key must not be empty or a placeholder")
    await vault_repository.store_key(db, provider_id, body.api_key)
    logger.info("vault: stored key for provider '%s'", provider_id)
    await audit_repository.record(
        db, user.id, "key.update", resource=provider_id,
        ip=request.client.host if request.client else None,
    )
    return {'ok': True, 'configured': True, 'vaulted': True}


@router.delete('/{provider_id}/key', status_code=204)
async def delete_provider_key(
    provider_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
) -> None:
    """Remove a vaulted key; provider will fall back to the env variable."""
    if provider_id not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    await vault_repository.delete_key(db, provider_id)
    logger.info("vault: deleted key for provider '%s'", provider_id)
    await audit_repository.record(
        db, user.id, "key.delete", resource=provider_id,
        ip=request.client.host if request.client else None,
    )


@router.post('/{provider_id}/discover')
async def discover_provider_models(provider_id: str) -> dict:
    """Fetch the provider's live model catalog and persist it in the discovered catalog."""
    descriptor = get_descriptor(provider_id)
    if descriptor is None:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    if descriptor.discover is not None:
        try:
            models = await descriptor.discover()
        except DiscoveryError as exc:
            if descriptor.static_models:
                logger.warning(
                    "discovery for '%s' failed (%s); using static fallback", provider_id, exc.detail
                )
                models = [dict(m) for m in descriptor.static_models]
            else:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    elif descriptor.static_models:
        models = [dict(m) for m in descriptor.static_models]
    else:
        raise HTTPException(
            status_code=400, detail=f"Provider '{provider_id}' does not support model discovery"
        )

    discovered_catalog.save_provider_models(provider_id, models)
    entry = discovered_catalog.get_provider_entry(provider_id)
    logger.info("discovery: saved %d model(s) for provider '%s'", len(models), provider_id)
    return {
        'provider_id': provider_id,
        'model_count': len(models),
        'models': models,
        'discovered_at': entry.get('discovered_at'),
        'saved': True,
    }


@router.post('/{provider_id}/test')
async def test_provider(provider_id: str) -> ProviderTestResult:
    if provider_id == 'mock':
        return ProviderTestResult(provider_id=provider_id, ok=True, latency_ms=0, model_count=1)

    if provider_id == 'ollama':
        url = f"{settings.ollama_api_base.rstrip('/')}/api/tags"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
            latency_ms = int((time.perf_counter() - started) * 1000)
            return ProviderTestResult(
                provider_id=provider_id,
                ok=True,
                latency_ms=latency_ms,
                model_count=len(data.get('models', [])),
            )
        except (httpx.HTTPError, OSError) as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.warning('Ollama test failed: %s', exc)
            return ProviderTestResult(
                provider_id=provider_id, ok=False, latency_ms=latency_ms, error=str(exc)
            )

    descriptor = get_descriptor(provider_id)

    if not key_resolver.is_configured(provider_id):
        hint = (descriptor.key_hint if descriptor else None) or 'API_KEY'
        return ProviderTestResult(provider_id=provider_id, ok=False, error=f'{hint} is not set')

    # For cloud providers, send a minimal completion to verify the key actually works.
    test_model = descriptor.test_model if descriptor else None
    if not test_model:
        # Provider known but no test model defined — key presence is the best we can do.
        return ProviderTestResult(provider_id=provider_id, ok=True)

    from app.dependencies.provider_factory import get_provider
    from app.schemas.chat import ChatCompletionRequest, ChatMessage

    started = time.perf_counter()
    try:
        provider = get_provider(test_model)
        req = ChatCompletionRequest(
            model=test_model,
            messages=[ChatMessage(role="user", content="Reply with the single word: ok")],
            max_tokens=5,
            temperature=0.0,
        )
        await provider.complete(req)
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ProviderTestResult(provider_id=provider_id, ok=True, latency_ms=latency_ms)
    except Exception as exc:  # noqa: BLE001 — surface any API error to the caller
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.warning('Provider %s test completion failed: %s', provider_id, exc)
        return ProviderTestResult(
            provider_id=provider_id, ok=False, latency_ms=latency_ms, error=str(exc)
        )
