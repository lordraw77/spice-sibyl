import json
import logging
import sys
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException

from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/v1/models"


def pretty_label(name: str) -> str:
    return f"OpenRouter · {name.replace(":"," · ")}"


def is_free(model: dict) -> bool:
    pricing = model.get("pricing") or {}
    try:
        prompt_price = float(pricing.get("prompt", 0) or 0)
        completion_price = float(pricing.get("completion", 0) or 0)
        return prompt_price == 0.0 and completion_price == 0.0
    except (ValueError, TypeError):
        return False


def capabilities_from_model(model: dict) -> list[str]:
    arch = model.get("architecture") or {}
    params = model.get("supported_parameters") or []
    input_modalities = arch.get("input_modalities") or []
    output_modalities = arch.get("output_modalities") or []
    modality = arch.get("modality", "")
    caps = []

    if "text" in input_modalities and "text" in output_modalities:
        caps.append("chat")
    if "image" in input_modalities or "image" in modality.lower():
        caps.append("vision")
    if "audio" in input_modalities:
        caps.append("audio")
    if "tools" in params or "tool_choice" in params:
        caps.append("tools")
    if "response_format" in params or "structured_outputs" in params:
        caps.append("json")
    if "reasoning" in params or "thinking" in params:
        caps.append("reasoning")
    if "image" in output_modalities:
        caps.append("image_generation")

    seen = set()
    return [c for c in caps if not (c in seen or seen.add(c))]


def build_yaml_block(chat_models: list[dict]) -> str:
    lines = [
        "openrouter:",
        "  enabled: true",
        "  configured: true",
        "  models:",
        "      - id: openrouter/openrouter/free",
        "        label: OpenRouter · Free",
        "        default: false",
        "        free: true",
        "        capabilities: [chat]",
    ]

    for m in chat_models:
        model_id = m.get("id", "")
        name = m.get("name", model_id)
        free = "true" if is_free(m) else "false"
        capabilities = capabilities_from_model(m) or ["chat"]
        cap_str = ", ".join(capabilities)

        lines.append(f"      - id: openrouter/{model_id}")
        lines.append(f"        label: {pretty_label(name)}")
        lines.append("        default: false")
        lines.append(f"        free: {free}")
        lines.append(f"        capabilities: [{cap_str}]")

    return "\n".join(lines)


@router.post("/run")
async def run_openrouter_discovery():
    token = settings.openrouter_api_key

    if not token:
        raise HTTPException(
            status_code=400,
            detail="OPENROUTER_API_KEY non configurata nel .env del backend.",
        )

    try:
        req = Request(
            URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "spice-sibyl-openrouter-discovery/1.0",
            },
        )
        with urlopen(req, timeout=20) as r:
            data = json.load(r)
    except Exception as exc:
        logger.exception("OpenRouter API call failed")
        raise HTTPException(status_code=502, detail=f"Errore chiamata OpenRouter API: {exc}") from exc

    models = data.get("data") or []
    chat_models = [
        m for m in models
        if "text" in ((m.get("architecture") or {}).get("input_modalities") or [])
        and "text" in ((m.get("architecture") or {}).get("output_modalities") or [])
    ]
    chat_models.sort(key=lambda m: m.get("id", ""))

    yaml_block = build_yaml_block(chat_models)

    preview_models = [
        {
            "id": "openrouter/openrouter/free",
            "name": "openrouter/free",
            "label": "OpenRouter · Free",
            "free": True,
            "capabilities": ["chat"],
        }
    ] + [
        {
            "id": f"openrouter/{m.get('id', '')}",
            "name": m.get("id", ""),
            "label": pretty_label(m.get("name", m.get("id", ""))),
            "free": is_free(m),
            "capabilities": capabilities_from_model(m) or ["chat"],
        }
        for m in chat_models
    ]

    return {
        "model_count": len(preview_models),
        "yaml": yaml_block,
        "models": preview_models,
    }