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
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    cerebras_discovery,
    chat,
    cloudflare_discovery,
    gemini_discovery,
    groq_discovery,
    health,
    mistral_discovery,
    models,
    openrouter_discovery,
    providers,
)

api_router = APIRouter(prefix="/v1")

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(providers.router, prefix="/providers", tags=["providers"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(cloudflare_discovery.router, prefix="/cloudflare-discovery", tags=["cloudflare-discovery"])
api_router.include_router(openrouter_discovery.router, prefix="/openrouter-discovery", tags=["openrouter-discovery"])
api_router.include_router(gemini_discovery.router, prefix="/gemini-discovery", tags=["gemini-discovery"])
api_router.include_router(groq_discovery.router, prefix="/groq-discovery", tags=["groq-discovery"])
api_router.include_router(cerebras_discovery.router, prefix="/cerebras-discovery", tags=["cerebras-discovery"])
api_router.include_router(mistral_discovery.router, prefix="/mistral-discovery", tags=["mistral-discovery"])
