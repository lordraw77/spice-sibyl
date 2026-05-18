from fastapi import APIRouter

from app.api.v1.endpoints import chat, health, models

api_router = APIRouter(prefix="/v1")

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])