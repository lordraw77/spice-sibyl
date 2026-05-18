import json
import logging
import re
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException

from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/models/search"


def pretty_label(name: str) -> str:
    s = re.sub(r'^@(?:cf|hf)/', '', name)
    parts = s.split('/')
    model = parts[-1] if parts else s
    words = model.replace('-', ' ').split()
    out = []
    for w in words:
        if re.fullmatch(r'\d+b', w, re.I):
            out.append(w[:-1] + 'B')
        elif w.lower() == 'sqlcoder':
            out.append('SQLCoder')
        else:
            out.append(w[:1].upper() + w[1:])
    label = ' '.join(out)
    label = re.sub(r'\bFp8\b', 'FP8', label)
    label = re.sub(r'\bAwq\b', 'AWQ', label)
    label = re.sub(r'\bInt8\b', 'INT8', label)
    label = re.sub(r'\bLora\b', 'LoRA', label)
    return f'Cloudflare · {label}'


def is_free(model: dict) -> bool:
    props = model.get('properties') or []
    price_prop = next((p for p in props if p.get('property_id') == 'price'), None)
    if not price_prop:
        return True
    value = price_prop.get('value')
    if value is None:
        return True
    if isinstance(value, list):
        return len(value) == 0
    return False


def capabilities_from_properties(model: dict) -> list[str]:
    props = {
        p.get('property_id'): p.get('value')
        for p in (model.get('properties') or [])
        if isinstance(p, dict)
    }
    caps = []
    if (model.get('task') or {}).get('name') == 'Text Generation':
        caps.append('chat')
    if props.get('function_calling') == 'true' or props.get('tool_calling') == 'true':
        caps.append('tools')
    if props.get('json_mode') == 'true' or props.get('structured_outputs') == 'true':
        caps.append('json')
    if props.get('reasoning') == 'true':
        caps.append('reasoning')
    if props.get('vision') == 'true' or props.get('image_input') == 'true':
        caps.append('vision')
    if props.get('audio_input') == 'true':
        caps.append('audio')
    if props.get('streaming') == 'true' or props.get('realtime') == 'true':
        caps.append('streaming')
    seen: set = set()
    ordered = []
    for c in caps:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def build_yaml_block(text_models: list[dict]) -> str:
    lines = [
        "cloudflare:",
        "  enabled: true",
        "  configured: true",
        "  models:",
    ]
    for m in text_models:
        name = m.get('name', '')
        free = 'true' if is_free(m) else 'false'
        capabilities = capabilities_from_properties(m) or ['chat']
        cap_str = ', '.join(capabilities)
        lines.append(f"      - id: cloudflare/{name}")
        lines.append(f"        label: {pretty_label(name)}")
        lines.append( "        default: false")
        lines.append(f"        free: {free}")
        lines.append(f"        capabilities: [{cap_str}]")
    return '\n'.join(lines)


@router.post('/run')
async def run_cloudflare_discovery():
    account_id = settings.cloudflare_account_id
    token = settings.cloudflare_api_key

    if not account_id or not token:
        raise HTTPException(
            status_code=400,
            detail='CLOUDFLARE_ACCOUNT_ID e CLOUDFLARE_API_KEY non configurati nel .env del backend.',
        )

    url = SEARCH_URL.format(account_id=account_id)
    try:
        req = Request(url, headers={'Authorization': f'Bearer {token}'})
        with urlopen(req, timeout=20) as r:
            data = json.load(r)
    except Exception as exc:
        logger.exception('Cloudflare API call failed')
        raise HTTPException(status_code=502, detail=f'Errore chiamata Cloudflare API: {exc}') from exc

    models = data.get('result') or []
    text_models = [m for m in models if ((m.get('task') or {}).get('name') == 'Text Generation')]
    text_models.sort(key=lambda m: m.get('name', ''))

    yaml_block = build_yaml_block(text_models)

    return {
        'model_count': len(text_models),
        'yaml': yaml_block,
        'models': [
            {
                'id': f"cloudflare/{m.get('name', '')}",
                'name': m.get('name', ''),
                'label': pretty_label(m.get('name', '')),
                'free': is_free(m),
                'capabilities': capabilities_from_properties(m) or ['chat'],
            }
            for m in text_models
        ],
    }
