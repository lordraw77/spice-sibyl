"""
POST /v1/images/generations — text-to-image generation endpoint.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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
async def create_image(payload: ImageGenerationRequest):
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
        return ImageGenerationResponse(**result)
    except ImageGenerationError as exc:
        logger.warning("Image generation failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Image generation error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
