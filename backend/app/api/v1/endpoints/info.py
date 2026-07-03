"""
GET /v1/info — service metadata for the web UI "Info" page.

Returns backend version, environment, runtime details, uptime, configured
providers and feature flags. Protected like the rest of the API.
"""

import os
import platform
import time

from fastapi import APIRouter

from app.api.v1.endpoints.health import _count_configured_providers
from app.core.config import settings
from app.services import cache_service

router = APIRouter()

# Process start time — module import happens once at boot.
_STARTED_AT = int(time.time())


@router.get("")
async def service_info():
    db_size: int | None = None
    try:
        db_size = os.path.getsize(settings.db_path)
    except OSError:
        pass

    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
        "api_base": "/api/v1",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "started_at": _STARTED_AT,
        "uptime_seconds": int(time.time()) - _STARTED_AT,
        "default_model": settings.default_model,
        "timezone": settings.timezone,
        "providers_configured": _count_configured_providers(),
        "db": {"path": settings.db_path, "size_bytes": db_size},
        "response_cache": cache_service.stats(),
        "features": {
            "memory": settings.memory_enabled,
            "auto_title": settings.auto_title_enabled,
            "response_cache": settings.response_cache_enabled,
            "code_interpreter": settings.code_interpreter_enabled,
            "telegram_bot": bool(settings.telegram_bot_token),
            "backup": settings.backup_enabled,
            "rag_hybrid": settings.rag_hybrid,
            "discovery_refresh": settings.discovery_refresh_enabled,
            "chat_fallback_chain": bool(settings.chat_fallback_chain),
            "orchestrator": bool(settings.orchestrator_base_url),
        },
    }
