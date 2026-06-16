"""
POST /v1/nvidia-discovery/run — fetch the NVIDIA NIM model catalog.

Queries the NVIDIA NIM OpenAI-compatible /models API, filters to chat/LLM
models, and returns:
  - a ready-to-paste YAML block for provider_models.yaml
  - a structured model list with inferred capabilities

Requires NVIDIA_API_KEY in the environment (obtain at https://build.nvidia.com).
"""

import logging

import httpx

from fastapi import APIRouter, HTTPException

from app.services import key_resolver

router = APIRouter()
logger = logging.getLogger(__name__)

LIST_URL = "https://integrate.api.nvidia.com/v1/models"

# Model types that are NOT chat/LLM — skip them in the catalog
_SKIP_TYPES = frozenset({"embedding", "reranking", "vlm-only", "audio", "image"})


def pretty_label(model_id: str) -> str:
    """Convert 'meta/llama-3.1-70b-instruct' → 'NVIDIA · Meta Llama 3.1 70B Instruct'."""
    # Use the last part after the org prefix (e.g. 'meta/llama-...' → 'llama-...')
    name = model_id.split("/")[-1]
    words = name.replace("-", " ").replace(".", " ").replace("_", " ").split()
    out = []
    for w in words:
        if w.replace(".", "").isdigit():
            out.append(w)
        elif len(w) <= 3 and w.isalpha() and w.lower() not in ("the", "and", "for"):
            out.append(w.upper())
        else:
            out.append(w.capitalize())
    return f"NVIDIA · {' '.join(out)}"


def capabilities_from_model(model_id: str) -> list[str]:
    """Infer capabilities from the model identifier."""
    lower = model_id.lower()
    caps = ["chat"]

    if any(k in lower for k in ("vision", "vl", "vlm", "llava", "visual")):
        caps.append("vision")
    if any(k in lower for k in ("code", "coder", "codegen", "starcoder")):
        caps.append("code")
    if any(k in lower for k in ("nemotron", "r1", "reasoning", "think")):
        caps.append("reasoning")
    # Most NVIDIA NIM chat models support tool use
    caps.append("tools")

    seen: set = set()
    return [c for c in caps if not (c in seen or seen.add(c))]


def _is_chat_model(model: dict) -> bool:
    """Return True if the model is a chat/LLM model worth including."""
    model_type = (model.get("model_type") or "").lower()
    if model_type in _SKIP_TYPES:
        return False
    # Some entries have no model_type; include by default unless name signals non-chat
    model_id = (model.get("id") or "").lower()
    if any(k in model_id for k in ("embed", "rerank", "whisper", "tts", "diffusion", "stable")):
        return False
    return True


def build_yaml_block(models: list[dict]) -> str:
    """Render a YAML snippet ready to paste into the nvidia section of provider_models.yaml."""
    lines = [
        "nvidia:",
        "  enabled: true",
        "  configured: true",
        "  models:",
    ]
    for m in models:
        model_id = m.get("id", "")
        capabilities = capabilities_from_model(model_id)
        cap_str = ", ".join(capabilities)
        lines.append(f"      - id: nvidia/{model_id}")
        lines.append(f"        label: {pretty_label(model_id)}")
        lines.append("        default: false")
        lines.append("        free: false")
        lines.append(f"        capabilities: [{cap_str}]")
    return "\n".join(lines)


@router.post("/run")
async def run_nvidia_discovery():
    """Fetch all LLM models from the NVIDIA NIM catalog and generate the YAML config block."""
    api_key = key_resolver.resolve("nvidia")

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="NVIDIA_API_KEY is not configured. Set it in the backend .env or via the Providers page.",
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
        logger.exception("NVIDIA API call failed")
        raise HTTPException(status_code=502, detail=f"NVIDIA API error: {exc}") from exc

    all_models = data.get("data") or []
    chat_models = [m for m in all_models if _is_chat_model(m)]
    chat_models.sort(key=lambda m: m.get("id", ""))

    yaml_block = build_yaml_block(chat_models)

    return {
        "model_count": len(chat_models),
        "yaml": yaml_block,
        "models": [
            {
                "id": f"nvidia/{m.get('id', '')}",
                "name": m.get("id", ""),
                "label": pretty_label(m.get("id", "")),
                "free": False,
                "capabilities": capabilities_from_model(m.get("id", "")),
            }
            for m in chat_models
        ],
    }
