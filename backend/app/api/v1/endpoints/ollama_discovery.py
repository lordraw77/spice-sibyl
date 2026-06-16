"""
POST /v1/ollama-discovery/run — fetch the locally running Ollama model catalog.

Queries the Ollama REST API at the configured OLLAMA_API_BASE (/api/tags),
lists all pulled models, and returns:
  - a ready-to-paste YAML block for provider_models.yaml
  - a structured model list with inferred capabilities

No API key is required; Ollama must be reachable at OLLAMA_API_BASE.
"""

import logging

import httpx

from fastapi import APIRouter, HTTPException

from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


def _base_url() -> str:
    return (settings.ollama_api_base or "http://localhost:11434").rstrip("/")


def pretty_label(model_name: str) -> str:
    """Convert 'qwen2.5:7b-instruct' → 'Ollama · Qwen2.5 7B Instruct'."""
    # Strip tag from name (e.g. 'llama3:8b' → 'llama3 8b')
    name = model_name.replace(":", " ").replace("-", " ").replace(".", " ")
    words = name.split()
    out = []
    for w in words:
        if w.replace(".", "").isdigit():
            out.append(w)
        elif len(w) <= 3 and w.isalpha() and w.lower() not in ("the", "and", "for"):
            out.append(w.upper())
        else:
            out.append(w.capitalize())
    return f"Ollama · {' '.join(out)}"


def capabilities_from_model(model_name: str) -> list[str]:
    """Infer capabilities from the model name."""
    lower = model_name.lower()
    caps = ["chat"]

    if any(k in lower for k in ("code", "coder", "starcoder", "deepseek-coder", "codellama")):
        caps.append("code")
    if any(k in lower for k in ("vision", "llava", "minicpm-v", "moondream", "bakllava")):
        caps.append("vision")
    if any(k in lower for k in ("think", "r1", "reasoning", "qwq")):
        caps.append("reasoning")

    seen: set = set()
    return [c for c in caps if not (c in seen or seen.add(c))]


def build_yaml_block(models: list[dict]) -> str:
    """Render a YAML snippet ready to paste into the ollama section of provider_models.yaml."""
    lines = [
        "ollama:",
        "  enabled: true",
        "  models:",
    ]
    for m in models:
        name = m.get("name", "")
        capabilities = capabilities_from_model(name)
        cap_str = ", ".join(capabilities)
        lines.append(f"      - id: ollama/{name}")
        lines.append(f"        label: {pretty_label(name)}")
        lines.append("        default: false")
        lines.append("        free: true")
        lines.append(f"        capabilities: [{cap_str}]")
    return "\n".join(lines)


@router.post("/run")
async def run_ollama_discovery():
    """Fetch all pulled models from the local Ollama instance and generate the YAML config block."""
    api_base = _base_url()
    tags_url = f"{api_base}/api/tags"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(tags_url)
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot reach Ollama at {api_base}. Make sure Ollama is running.",
        ) from exc
    except Exception as exc:
        logger.exception("Ollama API call failed")
        raise HTTPException(status_code=502, detail=f"Ollama API error: {exc}") from exc

    all_models: list[dict] = data.get("models") or []
    all_models.sort(key=lambda m: m.get("name", ""))

    yaml_block = build_yaml_block(all_models)

    return {
        "model_count": len(all_models),
        "yaml": yaml_block,
        "models": [
            {
                "id": f"ollama/{m.get('name', '')}",
                "name": m.get("name", ""),
                "label": pretty_label(m.get("name", "")),
                "free": True,
                "capabilities": capabilities_from_model(m.get("name", "")),
            }
            for m in all_models
        ],
    }
