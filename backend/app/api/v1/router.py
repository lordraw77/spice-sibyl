from fastapi import APIRouter

from app.api.v1.endpoints import (
    chat,
    cloudflare_discovery,
    health,
    models,
    openrouter_discovery,
)

api_router = APIRouter(prefix="/v1")

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(cloudflare_discovery.router, prefix="/cloudflare-discovery", tags=["cloudflare-discovery"])
api_router.include_router(openrouter_discovery.router, prefix="/openrouter-discovery", tags=["openrouter-discovery"])
