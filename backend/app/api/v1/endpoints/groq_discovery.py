"""
POST /v1/groq-discovery/run — fetch the Groq model catalog.

Queries the Groq OpenAI-compatible /models API, filters to active LLM models
(excludes Whisper, TTS, and guard models), and returns:
  - a ready-to-paste YAML block for provider_models.yaml
  - a structured model list with capabilities

Requires GROQ_API_KEY in the environment.
"""

import logging

import httpx

from fastapi import APIRouter, HTTPException

from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

LIST_URL = "https://api.groq.com/openai/v1/models"

# Model id substrings that indicate non-LLM models to exclude
_EXCLUDE_PATTERNS = ("whisper", "tts", "guard", "playai")


def is_excluded(model_id: str) -> bool:
    lower = model_id.lower()
    return any(p in lower for p in _EXCLUDE_PATTERNS)


def pretty_label(model_id: str) -> str:
    """Convert 'llama-3.3-70b-versatile' → 'Groq · Llama 3.3 70B Versatile'."""
    # Strip provider prefix if present (e.g. 'groq/...')
    name = model_id.split("/")[-1]
    # Replace separators and title-case words
    words = name.replace("-", " ").replace(".", " ").split()
    out = []
    for w in words:
        # Keep numeric-looking tokens (70b, 8b, 3.3) upper-ish
        if w.replace(".", "").isdigit():
            out.append(w)
        elif len(w) <= 3 and w.isalpha() and w.lower() not in ("the", "and", "for"):
            out.append(w.upper())
        else:
            out.append(w.capitalize())
    return f"Groq · {' '.join(out)}"


def capabilities_from_model(model_id: str) -> list[str]:
    """Infer capabilities from the model identifier."""
    lower = model_id.lower()
    caps = ["chat"]

    if "vision" in lower or "llava" in lower or "scout" in lower or "maverick" in lower:
        caps.append("vision")
    if "tool" in lower or any(
        f in lower for f in ("llama-3", "mixtral", "gemma", "qwen")
    ):
        caps.append("tools")
    if any(f in lower for f in ("llama-3", "mixtral", "qwen")):
        caps.append("json")
    if "deepseek" in lower and "r1" in lower:
        caps.append("reasoning")

    seen: set = set()
    return [c for c in caps if not (c in seen or seen.add(c))]


def build_yaml_block(models: list[dict]) -> str:
    """Render a YAML snippet ready to paste into the groq section of provider_models.yaml."""
    lines = [
        "groq:",
        "  enabled: true",
        "  configured: true",
        "  models:",
    ]
    for m in models:
        model_id = m.get("id", "")
        capabilities = capabilities_from_model(model_id)
        cap_str = ", ".join(capabilities)
        lines.append(f"      - id: groq/{model_id}")
        lines.append(f"        label: {pretty_label(model_id)}")
        lines.append("        default: false")
        lines.append("        free: false")
        lines.append(f"        capabilities: [{cap_str}]")
    return "\n".join(lines)


@router.post("/run")
async def run_groq_discovery():
    """Fetch all LLM models from the Groq catalog and generate the YAML config block."""
    api_key = settings.groq_api_key

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="GROQ_API_KEY is not configured in the backend .env.",
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
        logger.exception("Groq API call failed")
        raise HTTPException(status_code=502, detail=f"Groq API error: {exc}") from exc

    all_models = data.get("data") or []
    llm_models = [
        m for m in all_models
        if not is_excluded(m.get("id", "")) and m.get("active", True)
    ]
    llm_models.sort(key=lambda m: m.get("id", ""))

    yaml_block = build_yaml_block(llm_models)

    return {
        "model_count": len(llm_models),
        "yaml": yaml_block,
        "models": [
            {
                "id": f"groq/{m.get('id', '')}",
                "name": m.get("id", ""),
                "label": pretty_label(m.get("id", "")),
                "free": False,
                "capabilities": capabilities_from_model(m.get("id", "")),
            }
            for m in llm_models
        ],
    }
