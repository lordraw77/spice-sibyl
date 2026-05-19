"""
POST /v1/gemini-discovery/run — fetch the Google Gemini model catalog.

Queries the Google Generative Language /models API, filters to models that
support generateContent (text generation), and returns:
  - a ready-to-paste YAML block for provider_models.yaml
  - a structured model list with capabilities

Requires GEMINI_API_KEY in the environment.
"""

import logging

import httpx

from fastapi import APIRouter, HTTPException

from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

LIST_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Models known to be available on the free tier
_FREE_TIER_PATTERNS = ("flash", "gemini-1.0-pro", "gemini-pro")


def model_short_id(name: str) -> str:
    """Strip the 'models/' prefix from the API resource name."""
    return name.removeprefix("models/")


def pretty_label(name: str) -> str:
    """Convert 'models/gemini-2.0-flash' to 'Gemini · Gemini 2.0 Flash'."""
    short = model_short_id(name)
    parts = short.replace("-", " ").split()
    label = " ".join(w.capitalize() for w in parts)
    return f"Gemini · {label}"


def is_free(model: dict) -> bool:
    """Return True for models available on the free tier (inferred from name)."""
    name = model.get("name", "").lower()
    return any(p in name for p in _FREE_TIER_PATTERNS)


def capabilities_from_model(model: dict) -> list[str]:
    """
    Derive capabilities from the model name and supportedGenerationMethods.

    Recognized capabilities: chat, vision, audio, tools, json, reasoning.
    """
    name = model_short_id(model.get("name", "")).lower()
    methods = model.get("supportedGenerationMethods") or []
    caps = []

    if "generatecontent" in [m.lower() for m in methods]:
        caps.append("chat")

    # Gemini 1.5+ and 2.x support multimodal input
    if "vision" in name or any(f"gemini-{v}" in name for v in ("1.5", "2.0", "2.5")):
        caps.append("vision")

    # Audio understanding: 1.5-pro and later
    if "1.5-pro" in name or "2.0" in name or "2.5" in name:
        caps.append("audio")

    # Function calling / tool use
    if any(f"gemini-{v}" in name for v in ("1.5", "2.0", "2.5")):
        caps.append("tools")
        caps.append("json")

    # Explicit reasoning/thinking models
    if "thinking" in name or "reasoning" in name:
        caps.append("reasoning")

    seen: set = set()
    return [c for c in caps if not (c in seen or seen.add(c))]


def build_yaml_block(models: list[dict]) -> str:
    """Render a YAML snippet ready to paste into the gemini section of provider_models.yaml."""
    lines = [
        "gemini:",
        "  enabled: true",
        "  configured: true",
        "  models:",
    ]
    for m in models:
        short_id = model_short_id(m.get("name", ""))
        free = "true" if is_free(m) else "false"
        capabilities = capabilities_from_model(m) or ["chat"]
        cap_str = ", ".join(capabilities)
        lines.append(f"      - id: gemini/{short_id}")
        lines.append(f"        label: {pretty_label(m.get('name', short_id))}")
        lines.append("        default: false")
        lines.append(f"        free: {free}")
        lines.append(f"        capabilities: [{cap_str}]")
    return "\n".join(lines)


@router.post("/run")
async def run_gemini_discovery():
    """Fetch all generateContent-capable models from the Gemini API and generate the YAML config block."""
    api_key = settings.gemini_api_key

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="GEMINI_API_KEY is not configured in the backend .env.",
        )

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(LIST_URL, params={"key": api_key})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.exception("Gemini API call failed")
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}") from exc

    all_models = data.get("models") or []
    text_models = [
        m for m in all_models
        if "generateContent" in (m.get("supportedGenerationMethods") or [])
    ]
    text_models.sort(key=lambda m: m.get("name", ""))

    yaml_block = build_yaml_block(text_models)

    return {
        "model_count": len(text_models),
        "yaml": yaml_block,
        "models": [
            {
                "id": f"gemini/{model_short_id(m.get('name', ''))}",
                "name": model_short_id(m.get("name", "")),
                "label": pretty_label(m.get("name", "")),
                "free": is_free(m),
                "capabilities": capabilities_from_model(m) or ["chat"],
            }
            for m in text_models
        ],
    }
