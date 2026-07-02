"""
Model catalog — the runtime-discovered model registry.

The catalog is built from two sources, both keyed on the provider registry
(app.providers.registry.PROVIDERS):

  1. discovered models persisted by POST /v1/providers/{id}/discover
     (app.data.discovered_catalog, refreshed automatically at startup)
  2. static_models declared on the descriptor, used when a provider has no
     discovery result yet (mock, agent fallback)

Per-provider runtime overrides (app.data.runtime_config) control the enabled
flag and an optional default_model; the global settings.default_model marks
the fallback default. The legacy provider_models.yaml file is gone — models
appear by running discovery, not by editing configuration.
"""

from typing import Any

from app.core.config import settings
from app.data import discovered_catalog
from app.data.runtime_config import get_provider_override


def _owner(provider_key: str) -> str:
    """Map provider keys to canonical owner names for the 'owned_by' field."""
    if provider_key == 'gemini':
        return 'google'
    if provider_key == 'mock':
        return 'spice-sibyl'
    if provider_key == 'agent':
        return 'multi-mcp'
    return provider_key


def _registry():
    # Imported lazily: registry → provider classes → this module
    from app.providers.registry import PROVIDERS
    return PROVIDERS


def _is_enabled(provider_id: str, default: bool) -> bool:
    return get_provider_override(provider_id).get('enabled', default)


def _provider_models(provider_id: str, descriptor) -> list[dict[str, Any]]:
    """Discovered models for a provider, falling back to the descriptor's static list."""
    models = discovered_catalog.get_provider_entry(provider_id).get('models')
    if not models and descriptor.static_models:
        models = [dict(m) for m in descriptor.static_models]
    return models or []


def iter_configured_models() -> list[dict[str, Any]]:
    """Yield normalized model dicts for all enabled providers in the registry."""
    from app.services import key_resolver

    output: list[dict[str, Any]] = []
    seen: set = set()
    for provider_id, descriptor in _registry().items():
        if not _is_enabled(provider_id, descriptor.enabled_by_default):
            continue
        default_model = get_provider_override(provider_id).get('default_model')
        configured = key_resolver.is_configured(provider_id)
        for model in _provider_models(provider_id, descriptor):
            if model['id'] in seen:
                continue
            seen.add(model['id'])
            output.append(
                {
                    'id': model['id'],
                    'object': 'model',
                    'owned_by': _owner(provider_id),
                    'label': model.get('label', model['id']),
                    'provider': provider_id,
                    'enabled': True,
                    'configured': configured,
                    'default': model['id'] in (default_model, settings.default_model),
                    'free': model.get('free', False),
                    'capabilities': model.get('capabilities', []),
                }
            )
    return output


def provider_summary_from_catalog() -> list[dict[str, Any]]:
    """Build a per-provider summary from the registry and the discovered catalog."""
    output: list[dict[str, Any]] = []
    for provider_id, descriptor in _registry().items():
        models = _provider_models(provider_id, descriptor)
        output.append(
            {
                'id': provider_id,
                'label': descriptor.label,
                'enabled': _is_enabled(provider_id, descriptor.enabled_by_default),
                'configured': False,  # refined by the providers endpoint via key_resolver
                'model_count': len(models),
                'capabilities': sorted({cap for m in models for cap in m.get('capabilities', [])}),
            }
        )
    return output


def get_model_metadata(model_id: str) -> dict[str, Any]:
    """Look up catalog metadata for a model by ID; return safe defaults if not found."""
    for item in iter_configured_models():
        if item['id'] == model_id:
            return item
    return {
        'id': model_id,
        'label': model_id,
        'provider': model_id.split('/', 1)[0] if '/' in model_id else 'unknown',
        'configured': False,
        'free': False,
        'capabilities': ['chat'],
    }
