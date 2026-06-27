"""
Health & readiness probes (Phase 16).

  * GET /health — liveness; always cheap, returns 200 while the process is up.
                  Consumed by the Dockerfile HEALTHCHECK; contract must stay stable.
  * GET /ready  — readiness; verifies DB connectivity and that at least one chat
                  provider is configured. Returns 503 when a dependency is down so
                  orchestrators hold traffic until the service can actually serve.
"""

import logging

from fastapi import APIRouter, Response

from app.core.config import settings
from app.db.database import get_db
from app.services import key_resolver

logger = logging.getLogger(__name__)

# `router` is mounted at /v1/health (liveness); `ready_router` at /v1/ready so the
# readiness path matches the documented GET /api/v1/ready probe.
router = APIRouter()
ready_router = APIRouter()

# Providers whose configuration we probe for readiness. Ollama is always
# considered available (local, keyless) when an API base is set.
_KEYED_PROVIDERS = (
    "groq", "openrouter", "gemini", "cloudflare", "together_ai",
    "fireworks_ai", "mistral", "huggingface", "cerebras", "nvidia",
)


@router.get("")
async def healthcheck():
    return {"status": "ok"}


def _count_configured_providers() -> int:
    count = sum(1 for p in _KEYED_PROVIDERS if key_resolver.is_configured(p))
    if settings.ollama_api_base:
        count += 1
    return count


@ready_router.get("/ready")
async def readiness(response: Response):
    """Readiness probe — 200 when DB + at least one provider are available."""
    db_ok = False
    try:
        async for db in get_db():
            await db.execute("SELECT 1")
            db_ok = True
    except Exception:
        logger.exception("Readiness: DB check failed")

    providers = _count_configured_providers()
    ready = db_ok and providers > 0
    if not ready:
        response.status_code = 503
    return {
        "status": "ready" if ready else "not_ready",
        "checks": {"db": db_ok, "providers": providers},
    }
