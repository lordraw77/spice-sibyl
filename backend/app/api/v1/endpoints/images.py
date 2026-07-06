"""
POST /v1/images/generations — text-to-image generation endpoint.
"""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db.database import get_db
from app.dependencies.auth import resolve_profile
from app.services import notification_service
from app.services.image_service import generate_image, ImageGenerationError, get_available_provider

router = APIRouter()
logger = logging.getLogger(__name__)


class ImageGenerationRequest(BaseModel):
    prompt: str
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)
    provider: str | None = None


class ImageGenerationResponse(BaseModel):
    b64_json: str
    provider: str
    model: str


@router.post("/generations", response_model=ImageGenerationResponse)
async def create_image(
    payload: ImageGenerationRequest,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Generate an image from a text prompt."""
    available = get_available_provider()
    if not available and not payload.provider:
        raise HTTPException(
            status_code=503,
            detail="No image generation provider configured. Set GEMINI_API_KEY, HF_TOKEN, CLOUDFLARE_API_KEY, or TOGETHER_API_KEY.",
        )

    try:
        result = await generate_image(
            prompt=payload.prompt,
            width=payload.width,
            height=payload.height,
            provider=payload.provider,
        )
        # Phase 23.c: this endpoint is web-only (the Telegram /imagine command
        # calls generate_image() directly), so a linked profile always means the
        # generation was triggered from the web — safe to always notify.
        try:
            preview = payload.prompt.strip().replace("\n", " ")[:150]
            await notification_service.notify_telegram(
                db, profile_id, "imageGenDone", f"🖼️ Image ready: {preview}"
            )
        except Exception:  # noqa: BLE001 — notification failure must not fail the request
            logger.exception("Image generation: notify_telegram failed")
        return ImageGenerationResponse(**result)
    except ImageGenerationError as exc:
        logger.warning("Image generation failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Image generation error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
