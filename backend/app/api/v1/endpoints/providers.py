"""
GET    /v1/providers               — list all providers with live configuration status
POST   /v1/providers/{id}/test     — test connectivity to a provider
PATCH  /v1/providers/{id}          — enable or disable a provider
PUT    /v1/providers/{id}/key      — encrypt and vault an API key for a provider
DELETE /v1/providers/{id}/key      — remove a vaulted key
"""

import time
import logging

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.core.config import settings
from app.data.model_catalog import provider_summary_from_catalog
from app.data.runtime_config import get_provider_override, set_provider_override
from app.db.database import get_db
from app.db import vault_repository
from app.services import key_resolver
from app.schemas.providers import (
    ProviderKeyRequest,
    ProviderStatus,
    ProviderTestResult,
    ProviderUpdateRequest,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_PROVIDER_META: dict[str, dict] = {
    'ollama':       {'key_hint': None,                  'docs_url': 'https://ollama.com'},
    'groq':         {'key_hint': 'GROQ_API_KEY',        'docs_url': 'https://console.groq.com'},
    'openrouter':   {'key_hint': 'OPENROUTER_API_KEY',  'docs_url': 'https://openrouter.ai/keys'},
    'gemini':       {'key_hint': 'GEMINI_API_KEY',      'docs_url': 'https://aistudio.google.com'},
    'cloudflare':   {'key_hint': 'CLOUDFLARE_API_KEY',  'docs_url': 'https://dash.cloudflare.com'},
    'together_ai':  {'key_hint': 'TOGETHER_API_KEY',    'docs_url': 'https://api.together.xyz'},
    'fireworks_ai': {'key_hint': 'FIREWORKS_API_KEY',   'docs_url': 'https://fireworks.ai'},
    'mistral':      {'key_hint': 'MISTRAL_API_KEY',     'docs_url': 'https://console.mistral.ai'},
    'huggingface':  {'key_hint': 'HF_TOKEN',            'docs_url': 'https://huggingface.co/settings/tokens'},
    'openai':       {'key_hint': 'OPENAI_API_KEY',      'docs_url': 'https://platform.openai.com/api-keys'},
    'cerebras':     {'key_hint': 'CEREBRAS_API_KEY',    'docs_url': 'https://cloud.cerebras.ai'},
    'nvidia':       {'key_hint': 'NVIDIA_API_KEY',      'docs_url': 'https://build.nvidia.com'},
    'mock':         {'key_hint': None,                  'docs_url': None},
}


def _is_enabled(provider_id: str, catalog_enabled: bool) -> bool:
    override = get_provider_override(provider_id)
    return override.get('enabled', catalog_enabled)


def _build_status(entry: dict, pid: str) -> ProviderStatus:
    meta = _PROVIDER_META.get(pid, {})
    return ProviderStatus(
        id=pid,
        label=entry['label'],
        enabled=_is_enabled(pid, entry.get('enabled', True)),
        configured=key_resolver.is_configured(pid),
        key_hint=meta.get('key_hint'),
        model_count=entry['model_count'],
        capabilities=entry['capabilities'],
        docs_url=meta.get('docs_url'),
    )


@router.get('')
async def list_providers() -> list[ProviderStatus]:
    catalog = provider_summary_from_catalog()
    return [_build_status(entry, entry['id']) for entry in catalog]


@router.patch('/{provider_id}')
async def update_provider(provider_id: str, body: ProviderUpdateRequest) -> ProviderStatus:
    catalog = provider_summary_from_catalog()
    entry = next((e for e in catalog if e['id'] == provider_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    set_provider_override(provider_id, enabled=body.enabled)
    return _build_status(entry, provider_id)


@router.put('/{provider_id}/key')
async def set_provider_key(
    provider_id: str,
    body: ProviderKeyRequest,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    """Encrypt and store the API key in the vault (SQLite, AES via Fernet)."""
    if provider_id not in _PROVIDER_META:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    if not body.api_key or body.api_key in {'dummy', 'change-me', ''}:
        raise HTTPException(status_code=422, detail="api_key must not be empty or a placeholder")
    await vault_repository.store_key(db, provider_id, body.api_key)
    logger.info("vault: stored key for provider '%s'", provider_id)
    return {'ok': True, 'configured': True, 'vaulted': True}


@router.delete('/{provider_id}/key', status_code=204)
async def delete_provider_key(
    provider_id: str,
    db: aiosqlite.Connection = Depends(get_db),
) -> None:
    """Remove a vaulted key; provider will fall back to the env variable."""
    if provider_id not in _PROVIDER_META:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    await vault_repository.delete_key(db, provider_id)
    logger.info("vault: deleted key for provider '%s'", provider_id)


_TEST_MODELS: dict[str, str] = {
    'groq':         'groq/llama-3.1-8b-instant',
    'openrouter':   'openrouter/openai/gpt-4o-mini',
    'gemini':       'gemini/gemini-1.5-flash-latest',
    'mistral':      'mistral/mistral-small-latest',
    'cerebras':     'cerebras/llama3.1-8b',
    'nvidia':       'nvidia/meta/llama-3.1-8b-instruct',
    'openai':       'openai/gpt-4o-mini',
}

_TEST_MESSAGES = [{"role": "user", "content": "Reply with the single word: ok"}]


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

    if not key_resolver.is_configured(provider_id):
        meta = _PROVIDER_META.get(provider_id, {})
        hint = meta.get('key_hint') or 'API_KEY'
        return ProviderTestResult(provider_id=provider_id, ok=False, error=f'{hint} is not set')

    # For cloud providers, send a minimal completion to verify the key actually works.
    test_model = _TEST_MODELS.get(provider_id)
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
