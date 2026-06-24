"""
Image generation service — text-to-image via configurable provider chain.

The chain is read from IMAGE_GENERATION_CHAIN (comma-separated "provider:model"
pairs).  Each entry is tried in order; on failure the next is attempted.
Supported providers: gemini, huggingface, cloudflare, together_ai.
"""

import base64
import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.services import key_resolver

logger = logging.getLogger(__name__)


class ImageGenerationError(Exception):
    pass


@dataclass(frozen=True)
class _ChainEntry:
    provider: str
    model: str


def _parse_chain() -> list[_ChainEntry]:
    raw = settings.image_generation_chain or ""
    entries: list[_ChainEntry] = []
    for token in raw.split(","):
        token = token.strip()
        if ":" not in token:
            continue
        provider, model = token.split(":", 1)
        entries.append(_ChainEntry(provider=provider.strip(), model=model.strip()))
    return entries


def _is_provider_configured(provider: str) -> bool:
    if provider == "cloudflare":
        return bool(key_resolver.resolve("cloudflare") and settings.cloudflare_account_id)
    return bool(key_resolver.resolve(provider))


# ── Main entry point ───────────────────────────────────────────────────────

async def generate_image(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    provider: str | None = None,
) -> dict:
    """Generate an image from a text prompt.

    Walks the configured chain, skipping entries whose provider key is missing.
    Returns {"b64_json": str, "provider": str, "model": str}.
    """
    chain = _parse_chain()

    if provider:
        chain = [e for e in chain if e.provider == provider]

    errors: list[str] = []
    for entry in chain:
        if not _is_provider_configured(entry.provider):
            continue
        try:
            logger.info("Image generation: trying %s:%s", entry.provider, entry.model)
            result = await _CALLERS[entry.provider](prompt, width, height, entry.model)
            return result
        except Exception as exc:
            logger.warning("Image generation: %s:%s failed: %s", entry.provider, entry.model, exc)
            errors.append(f"{entry.provider}:{entry.model} → {exc}")

    if errors:
        raise ImageGenerationError(
            "All providers failed:\n" + "\n".join(errors)
        )
    raise ImageGenerationError(
        "No image generation provider configured. "
        "Check IMAGE_GENERATION_CHAIN and provider API keys."
    )


def get_available_provider() -> str | None:
    """Return the first configured provider in the chain, or None."""
    for entry in _parse_chain():
        if _is_provider_configured(entry.provider):
            return entry.provider
    return None


# ── Provider callers ───────────────────────────────────────────────────────

async def _call_gemini(prompt: str, width: int, height: int, model: str) -> dict:
    api_key = key_resolver.resolve("gemini")
    if not api_key:
        raise ImageGenerationError("API key not configured")

    base = "https://generativelanguage.googleapis.com/v1beta/models"

    if model.startswith("imagen-"):
        url = f"{base}/{model}:predict?key={api_key}"
        payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {"sampleCount": 1},
        }
    else:
        url = f"{base}/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(url, json=payload)

    if resp.status_code != 200:
        raise ImageGenerationError(f"HTTP {resp.status_code}")

    body = resp.json()

    if model.startswith("imagen-"):
        predictions = body.get("predictions") or []
        if not predictions:
            raise ImageGenerationError("no predictions")
        b64 = predictions[0].get("bytesBase64Encoded") or ""
        if not b64:
            raise ImageGenerationError("empty image data")
        return {"b64_json": b64, "provider": "gemini", "model": model}

    candidates = body.get("candidates") or []
    if not candidates:
        raise ImageGenerationError("no candidates")
    parts = candidates[0].get("content", {}).get("parts") or []
    for part in parts:
        inline = part.get("inlineData")
        if inline and inline.get("mimeType", "").startswith("image/"):
            return {"b64_json": inline["data"], "provider": "gemini", "model": model}

    raise ImageGenerationError("no image in response")


async def _call_huggingface(prompt: str, width: int, height: int, model: str) -> dict:
    api_key = key_resolver.resolve("huggingface")
    if not api_key:
        raise ImageGenerationError("API key not configured")

    url = f"https://api-inference.huggingface.co/models/{model}"

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"inputs": prompt, "parameters": {"width": width, "height": height}},
        )
        if resp.status_code == 503:
            raise ImageGenerationError("model is loading")
        if resp.status_code != 200:
            raise ImageGenerationError(f"HTTP {resp.status_code}")

        content_type = resp.headers.get("content-type", "")
        if "image/" not in content_type:
            raise ImageGenerationError("unexpected content type")

        b64 = base64.b64encode(resp.content).decode()

    return {"b64_json": b64, "provider": "huggingface", "model": model}


async def _call_cloudflare(prompt: str, width: int, height: int, model: str) -> dict:
    api_key = key_resolver.resolve("cloudflare")
    account_id = settings.cloudflare_account_id
    if not api_key or not account_id:
        raise ImageGenerationError("API key or account ID not configured")

    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"prompt": prompt, "width": width, "height": height},
        )
        if resp.status_code != 200:
            raise ImageGenerationError(f"HTTP {resp.status_code}")

        content_type = resp.headers.get("content-type", "")
        if "image/" in content_type:
            b64 = base64.b64encode(resp.content).decode()
        else:
            body = resp.json()
            result = body.get("result")
            if isinstance(result, dict):
                b64 = result.get("image") or ""
            else:
                raise ImageGenerationError("unexpected response format")

    if not b64:
        raise ImageGenerationError("empty image data")

    return {"b64_json": b64, "provider": "cloudflare", "model": model}


async def _call_together(prompt: str, width: int, height: int, model: str) -> dict:
    api_key = key_resolver.resolve("together_ai")
    if not api_key:
        raise ImageGenerationError("API key not configured")

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.together.xyz/v1/images/generations",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "prompt": prompt,
                "width": width,
                "height": height,
                "n": 1,
                "response_format": "b64_json",
            },
        )
        if resp.status_code != 200:
            raise ImageGenerationError(f"HTTP {resp.status_code}")
        body = resp.json()

    data_list = body.get("data") or []
    if not data_list:
        raise ImageGenerationError("no images returned")

    return {
        "b64_json": data_list[0].get("b64_json") or data_list[0].get("b64"),
        "provider": "together_ai",
        "model": model,
    }


_CALLERS = {
    "gemini": _call_gemini,
    "huggingface": _call_huggingface,
    "cloudflare": _call_cloudflare,
    "together_ai": _call_together,
}
