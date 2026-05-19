"""
GET    /v1/providers               — list all providers with live configuration status
POST   /v1/providers/{id}/test     — test connectivity to a provider
PATCH  /v1/providers/{id}          — enable or disable a provider
PUT    /v1/providers/{id}/key      — store an API key override for a provider
"""

import time
import logging

import httpx
from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.data.model_catalog import provider_summary_from_catalog
from app.data.runtime_config import get_provider_override, set_provider_override
from app.schemas.providers import (
    ProviderKeyRequest,
    ProviderStatus,
    ProviderTestResult,
    ProviderUpdateRequest,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Static metadata: which env var holds the key and where to get one
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
    'mock':         {'key_hint': None,                  'docs_url': None},
}

_PLACEHOLDER_KEYS = frozenset({'dummy', 'change-me', ''})


def _is_configured(provider_id: str) -> bool:
    """Return True if the provider has usable credentials (runtime override takes precedence)."""
    override = get_provider_override(provider_id)
    runtime_key = override.get('api_key', '')
    if runtime_key and runtime_key not in _PLACEHOLDER_KEYS:
        return True

    if provider_id in ('ollama', 'mock'):
        return True
    if provider_id == 'cloudflare':
        return bool(settings.cloudflare_api_key and settings.cloudflare_account_id)
    key_map: dict[str, str | None] = {
        'groq':         settings.groq_api_key,
        'openrouter':   settings.openrouter_api_key,
        'gemini':       settings.gemini_api_key,
        'together_ai':  settings.together_api_key,
        'fireworks_ai': settings.fireworks_api_key,
        'mistral':      settings.mistral_api_key,
        'huggingface':  settings.hf_token,
        'openai':       settings.openai_api_key,
        'cerebras':     settings.cerebras_api_key,
    }
    val = key_map.get(provider_id)
    return bool(val and val not in _PLACEHOLDER_KEYS)


def _is_enabled(provider_id: str, catalog_enabled: bool) -> bool:
    """Return enabled state, with runtime override taking precedence over catalog."""
    override = get_provider_override(provider_id)
    return override.get('enabled', catalog_enabled)


def _build_status(entry: dict, pid: str) -> ProviderStatus:
    meta = _PROVIDER_META.get(pid, {})
    return ProviderStatus(
        id=pid,
        label=entry['label'],
        enabled=_is_enabled(pid, entry.get('enabled', True)),
        configured=_is_configured(pid),
        key_hint=meta.get('key_hint'),
        model_count=entry['model_count'],
        capabilities=entry['capabilities'],
        docs_url=meta.get('docs_url'),
    )


@router.get('')
async def list_providers() -> list[ProviderStatus]:
    """Return all providers from the catalog with live configuration status."""
    catalog = provider_summary_from_catalog()
    return [_build_status(entry, entry['id']) for entry in catalog]


@router.patch('/{provider_id}')
async def update_provider(provider_id: str, body: ProviderUpdateRequest) -> ProviderStatus:
    """Enable or disable a provider (persisted in runtime_overrides.json)."""
    catalog = provider_summary_from_catalog()
    entry = next((e for e in catalog if e['id'] == provider_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    set_provider_override(provider_id, enabled=body.enabled)
    return _build_status(entry, provider_id)


@router.put('/{provider_id}/key')
async def set_provider_key(provider_id: str, body: ProviderKeyRequest) -> dict:
    """Store an API key override for a provider (persisted in runtime_overrides.json)."""
    if provider_id not in _PROVIDER_META:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    set_provider_override(provider_id, api_key=body.api_key)
    configured = bool(body.api_key and body.api_key not in _PLACEHOLDER_KEYS)
    return {'ok': True, 'configured': configured}


@router.post('/{provider_id}/test')
async def test_provider(provider_id: str) -> ProviderTestResult:
    """Test connectivity to a provider."""
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
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.warning('Ollama test failed: %s', exc)
            return ProviderTestResult(
                provider_id=provider_id,
                ok=False,
                latency_ms=latency_ms,
                error=str(exc),
            )

    if not _is_configured(provider_id):
        meta = _PROVIDER_META.get(provider_id, {})
        hint = meta.get('key_hint') or 'API_KEY'
        return ProviderTestResult(
            provider_id=provider_id,
            ok=False,
            error=f'{hint} is not set',
        )

    return ProviderTestResult(provider_id=provider_id, ok=True)
