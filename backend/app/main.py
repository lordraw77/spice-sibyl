"""
SpiceSibyl API — FastAPI application entry point.

Registers CORS middleware, mounts the v1 API router, and exposes a root
health/info endpoint.  All provider-specific logic lives under app/providers/.
"""

import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.database import get_db, init_db
from app.db import vault_repository

@asynccontextmanager
async def lifespan(application: FastAPI):
    await init_db()
    async for db in get_db():
        await vault_repository.load_all(db)

    # Start Telegram bot if a token is configured
    tg_app = None
    if settings.telegram_bot_token:
        from app.telegram.bot import build_application
        tg_app = build_application()
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        logging.getLogger(__name__).info("Telegram bot started (polling)")

    yield

    if tg_app:
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        logging.getLogger(__name__).info("Telegram bot stopped")


logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)-8s %(name)s — %(message)s',
)
# Keep noisy third-party loggers at WARNING
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('litellm').setLevel(logging.WARNING)

# Parse comma-separated origins from settings (supports env override)
origins = [item.strip() for item in settings.cors_origins.split(',') if item.strip()]

app = FastAPI(
    title=settings.app_name,
    version='0.2.0',
    description='OpenAI-compatible multi-provider AI gateway',
    lifespan=lifespan,
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
