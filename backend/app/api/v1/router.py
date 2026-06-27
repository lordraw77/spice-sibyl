"""
v1 API router — aggregates all endpoint sub-routers under the /v1 prefix.

Route map:
  GET  /v1/health                     — liveness probe
  GET  /v1/models                     — list available models
  GET  /v1/providers                  — list providers with configuration status
  POST /v1/providers/{id}/test        — test provider connectivity
  POST /v1/chat/completions           — chat completion (streaming or non-streaming)
  POST /v1/cloudflare-discovery/run   — fetch Cloudflare Workers AI model catalog
  POST /v1/openrouter-discovery/run   — fetch OpenRouter model catalog
  POST /v1/gemini-discovery/run       — fetch Google Gemini model catalog
  POST /v1/groq-discovery/run         — fetch Groq model catalog
  POST /v1/cerebras-discovery/run     — fetch Cerebras model catalog
  POST /v1/mistral-discovery/run      — fetch Mistral AI model catalog
  POST /v1/nvidia-discovery/run       — fetch NVIDIA NIM model catalog
  POST /v1/ollama-discovery/run       — fetch local Ollama model catalog
"""

from fastapi import APIRouter, Depends

from app.dependencies.auth import block_read_only
from app.dependencies.rate_limit import rate_limit
from app.api.v1.endpoints import (
    auth,
    cerebras_discovery,
    chat,
    cloudflare_discovery,
    conversations,
    gemini_discovery,
    groq_discovery,
    health,
    images,
    knowledge,
    mistral_discovery,
    models,
    nvidia_discovery,
    ollama_discovery,
    openrouter_discovery,
    profiles,
    providers,
    sharing,
    stats,
    tags,
    telegram_link,
    templates,
    tools,
)

api_router = APIRouter(prefix="/v1")

# Mandatory-auth guard applied to every protected sub-router. block_read_only
# transitively depends on get_current_user, so unauthenticated callers get 401
# and read-only accounts get 403 on mutating methods.
_protected = [Depends(block_read_only), Depends(rate_limit)]

# --- Public routers (no authentication) ---
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
# sharing exposes the public read-only GET /shared/{token}; the share/unshare
# mutations it also defines are gated inside the endpoint via get_current_user.
api_router.include_router(sharing.router, tags=["sharing"])

# --- Protected routers (JWT required) ---
api_router.include_router(models.router, prefix="/models", tags=["models"], dependencies=_protected)
api_router.include_router(providers.router, prefix="/providers", tags=["providers"], dependencies=_protected)
api_router.include_router(chat.router, prefix="/chat", tags=["chat"], dependencies=_protected)
api_router.include_router(cloudflare_discovery.router, prefix="/cloudflare-discovery", tags=["cloudflare-discovery"], dependencies=_protected)
api_router.include_router(openrouter_discovery.router, prefix="/openrouter-discovery", tags=["openrouter-discovery"], dependencies=_protected)
api_router.include_router(gemini_discovery.router, prefix="/gemini-discovery", tags=["gemini-discovery"], dependencies=_protected)
api_router.include_router(groq_discovery.router, prefix="/groq-discovery", tags=["groq-discovery"], dependencies=_protected)
api_router.include_router(cerebras_discovery.router, prefix="/cerebras-discovery", tags=["cerebras-discovery"], dependencies=_protected)
api_router.include_router(mistral_discovery.router, prefix="/mistral-discovery", tags=["mistral-discovery"], dependencies=_protected)
api_router.include_router(nvidia_discovery.router, prefix="/nvidia-discovery", tags=["nvidia-discovery"], dependencies=_protected)
api_router.include_router(ollama_discovery.router, prefix="/ollama-discovery", tags=["ollama-discovery"], dependencies=_protected)
api_router.include_router(images.router, prefix="/images", tags=["images"], dependencies=_protected)
api_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"], dependencies=_protected)
api_router.include_router(profiles.router, prefix="/profiles", tags=["profiles"], dependencies=_protected)
api_router.include_router(stats.router, prefix="/stats", tags=["stats"], dependencies=_protected)
api_router.include_router(tools.router, prefix="/tools", tags=["tools"], dependencies=_protected)
api_router.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"], dependencies=_protected)
api_router.include_router(templates.router, prefix="/templates", tags=["templates"], dependencies=_protected)
api_router.include_router(tags.router, prefix="/tags", tags=["tags"], dependencies=_protected)
api_router.include_router(telegram_link.router, prefix="/telegram", tags=["telegram"], dependencies=_protected)
