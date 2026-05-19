"""
SpiceSibyl API — FastAPI application entry point.

Registers CORS middleware, mounts the v1 API router, and exposes a root
health/info endpoint.  All provider-specific logic lives under app/providers/.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings

# Parse comma-separated origins from settings (supports env override)
origins = [item.strip() for item in settings.cors_origins.split(',') if item.strip()]

app = FastAPI(
    title=settings.app_name,
    version='0.2.0',
    description='OpenAI-compatible multi-provider AI gateway',
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(api_router, prefix='/api')


@app.get('/')
async def root():
    """Return basic service metadata — useful for quick health checks."""
    return {
        'name': settings.app_name,
        'environment': settings.app_env,
        'docs': '/docs',
        'api_base': '/api/v1',
        'default_model': settings.default_model,
    }
