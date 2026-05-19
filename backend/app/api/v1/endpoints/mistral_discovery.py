"""
POST /v1/mistral-discovery/run — fetch the Mistral AI model catalog.

Queries the Mistral /models API, filters to chat-capable models (excludes
embedding, FIM, and fine-tuned models), and returns:
  - a ready-to-paste YAML block for provider_models.yaml
  - a structured model list with capabilities

Requires MISTRAL_API_KEY in the environment.
"""

import logging

import httpx

from fastapi import APIRouter, HTTPException

from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

LIST_URL = 'https://api.mistral.ai/v1/models'


def is_chat_model(model: dict) -> bool:
    """Include only base chat models (not embeddings, FIM, or fine-tuned)."""
    caps = model.get('capabilities') or {}
    model_type = model.get('type', 'base')
    return (
        caps.get('completion_chat', False)
        and model_type in ('base', 'chat')
        and not model.get('id', '').startswith('ft:')
    )


def pretty_label(model_id: str) -> str:
    """Convert 'mistral-large-latest' → 'Mistral · Mistral Large Latest'."""
    name = model_id.split('/')[-1]
    words = name.replace('-', ' ').split()
    label = ' '.join(w.capitalize() for w in words)
    return f'Mistral · {label}'


def capabilities_from_model(model: dict) -> list[str]:
    """Derive capabilities from the Mistral capabilities object."""
    caps_obj = model.get('capabilities') or {}
    model_id = model.get('id', '').lower()
    caps = ['chat']

    if caps_obj.get('vision') or 'pixtral' in model_id:
        caps.append('vision')
    if caps_obj.get('function_calling'):
        caps.append('tools')
        caps.append('json')
    if 'codestral' in model_id or 'code' in model_id:
        caps.append('code')

    seen: set = set()
    return [c for c in caps if not (c in seen or seen.add(c))]


def build_yaml_block(models: list[dict]) -> str:
    """Render a YAML snippet ready to paste into the mistral section of provider_models.yaml."""
    lines = [
        'mistral:',
        '  enabled: true',
        '  configured: true',
        '  models:',
    ]
    for m in models:
        model_id = m.get('id', '')
        capabilities = capabilities_from_model(m)
        cap_str = ', '.join(capabilities)
        lines.append(f'      - id: mistral/{model_id}')
        lines.append(f'        label: {pretty_label(model_id)}')
        lines.append('        default: false')
        lines.append('        free: false')
        lines.append(f'        capabilities: [{cap_str}]')
    return '\n'.join(lines)


@router.post('/run')
async def run_mistral_discovery():
    """Fetch all chat models from the Mistral catalog and generate the YAML config block."""
    api_key = settings.mistral_api_key

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail='MISTRAL_API_KEY is not configured in the backend .env.',
        )

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                LIST_URL,
                headers={'Authorization': f'Bearer {api_key}'},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.exception('Mistral API call failed')
        raise HTTPException(status_code=502, detail=f'Mistral API error: {exc}') from exc

    all_models = data.get('data') or []
    chat_models = [m for m in all_models if is_chat_model(m)]
    chat_models.sort(key=lambda m: m.get('id', ''))

    yaml_block = build_yaml_block(chat_models)

    return {
        'model_count': len(chat_models),
        'yaml': yaml_block,
        'models': [
            {
                'id': f"mistral/{m.get('id', '')}",
                'name': m.get('id', ''),
                'label': pretty_label(m.get('id', '')),
                'free': False,
                'capabilities': capabilities_from_model(m),
            }
            for m in chat_models
        ],
    }
