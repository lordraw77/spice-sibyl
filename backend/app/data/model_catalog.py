"""
Model catalog — reads and merges the provider_models.yaml static configuration.

Catalog lookup order:
  1. MODEL_CATALOG_PATH env var (explicit override)
  2. /config/provider_models.yaml (Docker volume mount)
  3. provider_models.yaml next to this file (bundled fallback)

The merge_provider_summary function combines static catalog data with the
dynamic model list returned by providers (e.g. live Ollama models) so the
frontend sees a single, consistent provider summary.
"""

from pathlib import Path
from typing import Any
import os
import yaml

DEFAULT_CATALOG_PATH = Path('/config/provider_models.yaml')
FALLBACK_CATALOG_PATH = Path(__file__).with_name('provider_models.yaml')


def get_catalog_path() -> Path:
    """Resolve the active catalog path following the three-step lookup order."""
    override = os.getenv('MODEL_CATALOG_PATH')
    if override:
        return Path(override)
    if DEFAULT_CATALOG_PATH.exists():
        return DEFAULT_CATALOG_PATH
    return FALLBACK_CATALOG_PATH


def load_model_catalog() -> dict[str, Any]:
    """Parse and return the YAML catalog as a dict."""
    path = get_catalog_path()
    with path.open('r', encoding='utf-8') as fh:
        data = yaml.safe_load(fh) or {}
    return data


def provider_summary_from_catalog() -> list[dict[str, Any]]:
    """Build a per-provider summary from the static YAML catalog."""
    catalog = load_model_catalog()
    providers = catalog.get('providers', {})
    output: list[dict[str, Any]] = []
    for provider_key, provider in providers.items():
        models = provider.get('models', [])
        output.append(
            {
                'id': provider_key,
                'label': provider_key.replace('-', ' ').replace('_', ' ').title(),
                'enabled': provider.get('enabled', True),
                'configured': provider.get('configured', False),
                'model_count': len(models),
                # Aggregate all unique capabilities across models for this provider
                'capabilities': sorted({cap for model in models for cap in model.get('capabilities', [])}),
            }
        )
    return output


def iter_configured_models() -> list[dict[str, Any]]:
    """Yield normalized model dicts for all enabled providers in the catalog."""
    catalog = load_model_catalog()
    providers = catalog.get('providers', {})
    output: list[dict[str, Any]] = []
    for provider_key, provider in providers.items():
        if not provider.get('enabled', True):
            continue
        # Map provider keys to canonical owner names for the 'owned_by' field
        owner = 'google' if provider_key == 'gemini' else provider_key
        if provider_key == 'mock':
            owner = 'spice-sibyl'
        for model in provider.get('models', []):
            output.append(
                {
                    'id': model['id'],
                    'object': 'model',
                    'owned_by': owner,
                    'label': model.get('label', model['id']),
                    'provider': provider_key,
                    'enabled': True,
                    'configured': provider.get('configured', False),
                    'default': model.get('default', False),
                    'free': model.get('free', False),
                    'capabilities': model.get('capabilities', []),
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


def merge_provider_summary(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Merge the static catalog provider summary with a dynamic runtime model list.

    For each provider the resulting entry carries:
      - label and enabled flag from the catalog (authoritative)
      - model_count and configured from the dynamic list (live data)
      - capabilities as the union of static + dynamic sets
    """
    catalog_summary = {item['id']: item for item in provider_summary_from_catalog()}
    dynamic_summary: dict[str, dict[str, Any]] = {}

    for model in models:
        provider = model.get('provider') or 'unknown'
        item = dynamic_summary.setdefault(
            provider,
            {
                'id': provider,
                'label': provider.replace('-', ' ').replace('_', ' ').title(),
                'enabled': True,
                'configured': False,
                'model_count': 0,
                'capabilities': set(),
            },
        )
        item['model_count'] += 1
        item['configured'] = item['configured'] or bool(model.get('configured', False))
        for cap in model.get('capabilities', []) or []:
            item['capabilities'].add(cap)

    merged_keys = sorted(set(catalog_summary.keys()) | set(dynamic_summary.keys()))
    result: list[dict[str, Any]] = []

    for key in merged_keys:
        base = catalog_summary.get(
            key,
            {
                'id': key,
                'label': key.replace('-', ' ').replace('_', ' ').title(),
                'enabled': True,
                'configured': False,
                'model_count': 0,
                'capabilities': [],
            },
        )
        dyn = dynamic_summary.get(key)
        model_count = dyn['model_count'] if dyn else base['model_count']
        configured = base['configured'] or (dyn['configured'] if dyn else False)
        capabilities = set(base.get('capabilities', []))
        if dyn:
            capabilities |= dyn['capabilities']
        result.append(
            {
                'id': key,
                'label': base['label'],
                'enabled': base.get('enabled', True),
                'configured': configured,
                'model_count': model_count,
                'capabilities': sorted(capabilities),
            }
        )

    return result
