"""
v1 API router — aggregates all endpoint sub-routers under the /v1 prefix.

Route map:
  GET  /v1/health                     — liveness probe
  GET  /v1/models                     — list available models
  GET  /v1/providers                  — list providers with configuration status
  POST /v1/providers/{id}/test        — test provider connectivity
  POST /v1/providers/{id}/discover    — fetch and persist a provider's live model catalog
  POST /v1/chat/completions           — chat completion (streaming or non-streaming)
"""

from fastapi import APIRouter, Depends

from app.dependencies.auth import block_read_only
from app.dependencies.rate_limit import rate_limit
from app.api.v1.endpoints import (
    auth,
    chat,
    conversations,
    health,
    images,
    knowledge,
    admin,
    mcp,
    metrics,
    models,
    profiles,
    providers,
    sharing,
    stats,
    tags,
    telegram_link,
    templates,
    tools,
    workflows,
)

api_router = APIRouter(prefix="/v1")

# Mandatory-auth guard applied to every protected sub-router. block_read_only
# transitively depends on get_current_user, so unauthenticated callers get 401
# and read-only accounts get 403 on mutating methods.
_protected = [Depends(block_read_only), Depends(rate_limit)]

# --- Public routers (no authentication) ---
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(health.ready_router, tags=["health"])
# /metrics gates itself on settings.metrics_token (if set); kept off the JWT/rate
# guard so a Prometheus server can scrape it.
api_router.include_router(metrics.router, prefix="/metrics", tags=["metrics"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
# sharing exposes the public read-only GET /shared/{token}; the share/unshare
# mutations it also defines are gated inside the endpoint via get_current_user.
api_router.include_router(sharing.router, tags=["sharing"])

# --- Protected routers (JWT required) ---
api_router.include_router(models.router, prefix="/models", tags=["models"], dependencies=_protected)
api_router.include_router(providers.router, prefix="/providers", tags=["providers"], dependencies=_protected)
api_router.include_router(chat.router, prefix="/chat", tags=["chat"], dependencies=_protected)
api_router.include_router(images.router, prefix="/images", tags=["images"], dependencies=_protected)
api_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"], dependencies=_protected)
api_router.include_router(profiles.router, prefix="/profiles", tags=["profiles"], dependencies=_protected)
api_router.include_router(stats.router, prefix="/stats", tags=["stats"], dependencies=_protected)
api_router.include_router(tools.router, prefix="/tools", tags=["tools"], dependencies=_protected)
api_router.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"], dependencies=_protected)
api_router.include_router(templates.router, prefix="/templates", tags=["templates"], dependencies=_protected)
api_router.include_router(tags.router, prefix="/tags", tags=["tags"], dependencies=_protected)
api_router.include_router(telegram_link.router, prefix="/telegram", tags=["telegram"], dependencies=_protected)
api_router.include_router(admin.router, prefix="/admin", tags=["admin"], dependencies=_protected)
api_router.include_router(mcp.router, prefix="/mcp", tags=["mcp"], dependencies=_protected)
api_router.include_router(workflows.router, prefix="/workflows", tags=["workflows"], dependencies=_protected)
