"""
discovery_refresh — keeps the discovered model catalog fresh automatically.

Without the legacy YAML catalog, models only exist once discovery has run.
refresh_stale_providers() runs discovery for every registry provider that
has an adapter, is configured (key/URL present) and enabled, whose snapshot
is older than DISCOVERY_REFRESH_HOURS (or missing). refresh_loop() repeats
that check periodically; it is started from the FastAPI lifespan.

Failures are logged and skipped — an unreachable provider keeps serving its
last snapshot (or the descriptor's static_models fallback).
"""

import asyncio
import logging
import time

from app.core.config import settings
from app.data import discovered_catalog
from app.data.runtime_config import get_provider_override
from app.services.model_discovery import DiscoveryError

logger = logging.getLogger(__name__)


def _is_stale(provider_id: str, ttl_seconds: float) -> bool:
    entry = discovered_catalog.get_provider_entry(provider_id)
    discovered_at = entry.get('discovered_at')
    return discovered_at is None or (time.time() - discovered_at) >= ttl_seconds


async def refresh_stale_providers() -> int:
    """Run discovery for stale, enabled, configured providers; return refresh count."""
    from app.providers.registry import PROVIDERS
    from app.services import key_resolver

    ttl_seconds = settings.discovery_refresh_hours * 3600
    refreshed = 0
    for provider_id, descriptor in PROVIDERS.items():
        if descriptor.discover is None:
            continue
        if not get_provider_override(provider_id).get('enabled', descriptor.enabled_by_default):
            continue
        if not key_resolver.is_configured(provider_id):
            continue
        if not _is_stale(provider_id, ttl_seconds):
            continue
        try:
            models = await descriptor.discover()
        except DiscoveryError as exc:
            logger.warning("discovery refresh for '%s' failed: %s", provider_id, exc.detail)
            continue
        discovered_catalog.save_provider_models(provider_id, models)
        refreshed += 1
        logger.info("discovery refresh: saved %d model(s) for '%s'", len(models), provider_id)
    return refreshed


async def refresh_loop() -> None:
    """Periodic refresh, started at app startup. First pass runs immediately."""
    # Re-check well before the TTL expires so a transient failure gets retried
    interval = max(settings.discovery_refresh_hours * 3600 / 4, 300)
    while True:
        try:
            await refresh_stale_providers()
        except Exception:
            logger.exception('discovery refresh loop iteration failed')
        await asyncio.sleep(interval)
