"""
POST /v1/cerebras-discovery/run — fetch the Cerebras model catalog.

Queries the Cerebras OpenAI-compatible /models API, filters to active LLM
models, and returns:
  - a ready-to-paste YAML block for provider_models.yaml
  - a structured model list with capabilities

Requires CEREBRAS_API_KEY in the environment.
"""

import logging

import httpx

from fastapi import APIRouter, HTTPException

from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

LIST_URL = "https://api.cerebras.ai/v1/models"


def pretty_label(model_id: str) -> str:
    """Convert 'llama-3.3-70b' → 'Cerebras · Llama 3.3 70B'."""
    name = model_id.split("/")[-1]
    words = name.replace("-", " ").replace(".", " ").split()
    out = []
    for w in words:
        if w.replace(".", "").isdigit():
            out.append(w)
        elif len(w) <= 3 and w.isalpha() and w.lower() not in ("the", "and", "for"):
            out.append(w.upper())
        else:
            out.append(w.capitalize())
    return f"Cerebras · {' '.join(out)}"


def capabilities_from_model(model_id: str) -> list[str]:
    """Infer capabilities from the model identifier."""
    lower = model_id.lower()
    caps = ["chat"]

    # Llama 4 Scout / Maverick support vision
    if "scout" in lower or "maverick" in lower or "vision" in lower:
        caps.append("vision")

    # All current Cerebras models support tool use and JSON mode
    caps.append("tools")
    caps.append("json")

    # Reasoning / thinking models
    if "r1" in lower or "reasoning" in lower or "thinking" in lower:
        caps.append("reasoning")

    seen: set = set()
    return [c for c in caps if not (c in seen or seen.add(c))]


def build_yaml_block(models: list[dict]) -> str:
    """Render a YAML snippet ready to paste into the cerebras section of provider_models.yaml."""
    lines = [
        "cerebras:",
        "  enabled: true",
        "  configured: true",
        "  models:",
    ]
    for m in models:
        model_id = m.get("id", "")
        capabilities = capabilities_from_model(model_id)
        cap_str = ", ".join(capabilities)
        lines.append(f"      - id: cerebras/{model_id}")
        lines.append(f"        label: {pretty_label(model_id)}")
        lines.append("        default: false")
        lines.append("        free: false")
        lines.append(f"        capabilities: [{cap_str}]")
    return "\n".join(lines)


@router.post("/run")
async def run_cerebras_discovery():
    """Fetch all LLM models from the Cerebras catalog and generate the YAML config block."""
    api_key = settings.cerebras_api_key

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="CEREBRAS_API_KEY is not configured in the backend .env.",
        )

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                LIST_URL,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.exception("Cerebras API call failed")
        raise HTTPException(status_code=502, detail=f"Cerebras API error: {exc}") from exc

    all_models = data.get("data") or []
    # Cerebras returns only LLM models, no filtering needed
    llm_models = sorted(all_models, key=lambda m: m.get("id", ""))

    yaml_block = build_yaml_block(llm_models)

    return {
        "model_count": len(llm_models),
        "yaml": yaml_block,
        "models": [
            {
                "id": f"cerebras/{m.get('id', '')}",
                "name": m.get("id", ""),
                "label": pretty_label(m.get("id", "")),
                "free": False,
                "capabilities": capabilities_from_model(m.get("id", "")),
            }
            for m in llm_models
        ],
    }
