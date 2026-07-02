"""
model_discovery — per-provider adapters that fetch live model catalogs.

Each ``discover_<provider>`` coroutine queries the provider's model-listing
API and returns a normalized list of model dicts:

    {'id': 'groq/llama-3.3-70b', 'name': 'llama-3.3-70b',
     'label': 'Groq · Llama 3.3 70B', 'free': bool, 'capabilities': [...]}

Adapters are wired to providers via ``discover`` in
app.providers.registry.PROVIDERS and invoked by
POST /v1/providers/{id}/discover, which persists the result in
app.data.discovered_catalog.

API keys are resolved through key_resolver (vault first, then env), so keys
set from the Providers page work without a restart. Failures raise
DiscoveryError with an HTTP-ish status code the endpoint can surface.
"""

import logging
import re

import httpx

from app.core.config import settings
from app.services import key_resolver

logger = logging.getLogger(__name__)


class DiscoveryError(Exception):
    def __init__(self, detail: str, status_code: int = 502):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _dedup(caps: list[str]) -> list[str]:
    seen: set = set()
    return [c for c in caps if not (c in seen or seen.add(c))]


def _title_words(name: str) -> str:
    """'llama-3.3-70b-versatile' → 'Llama 3.3 70B Versatile' (shared label heuristic)."""
    words = name.replace('-', ' ').replace('.', ' ').replace('_', ' ').split()
    out = []
    for w in words:
        if w.replace('.', '').isdigit():
            out.append(w)
        elif re.fullmatch(r'\d+b', w, re.I):
            out.append(w[:-1] + 'B')
        elif len(w) <= 3 and w.isalpha() and w.lower() not in ('the', 'and', 'for'):
            out.append(w.upper())
        else:
            out.append(w.capitalize())
    return ' '.join(out)


def _require_key(provider_id: str, hint: str) -> str:
    key = key_resolver.resolve(provider_id)
    if not key:
        raise DiscoveryError(
            f'{hint} is not configured. Set it in the backend .env or via the Providers page.',
            status_code=400,
        )
    return key


async def _get_json(url: str, *, headers: dict | None = None, params: dict | None = None,
                    timeout: float = 20.0, provider_label: str = 'provider') -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError as exc:
        raise DiscoveryError(f'Cannot reach {provider_label} at {url}.') from exc
    except Exception as exc:
        logger.exception('%s API call failed', provider_label)
        raise DiscoveryError(f'{provider_label} API error: {exc}') from exc


def _entry(model_id: str, name: str, label: str, free: bool, capabilities: list[str]) -> dict:
    return {
        'id': model_id,
        'name': name,
        'label': label,
        'free': free,
        'capabilities': capabilities or ['chat'],
    }


# --------------------------------------------------------------------------- ollama

def _ollama_capabilities(model_name: str) -> list[str]:
    lower = model_name.lower()
    caps = ['chat']
    if any(k in lower for k in ('code', 'coder', 'starcoder', 'deepseek-coder', 'codellama')):
        caps.append('code')
    if any(k in lower for k in ('vision', 'llava', 'minicpm-v', 'moondream', 'bakllava')):
        caps.append('vision')
    if any(k in lower for k in ('think', 'r1', 'reasoning', 'qwq')):
        caps.append('reasoning')
    return _dedup(caps)


async def discover_ollama() -> list[dict]:
    base = (settings.ollama_api_base or 'http://localhost:11434').rstrip('/')
    data = await _get_json(f'{base}/api/tags', timeout=10.0, provider_label='Ollama')
    models = sorted(data.get('models') or [], key=lambda m: m.get('name', ''))
    return [
        _entry(
            f"ollama/{m.get('name', '')}",
            m.get('name', ''),
            f"Ollama · {_title_words(m.get('name', '').replace(':', ' '))}",
            free=True,
            capabilities=_ollama_capabilities(m.get('name', '')),
        )
        for m in models
    ]


# ----------------------------------------------------------------------------- groq

_GROQ_EXCLUDE = ('whisper', 'tts', 'guard', 'playai')


def _groq_capabilities(model_id: str) -> list[str]:
    lower = model_id.lower()
    caps = ['chat']
    if 'vision' in lower or 'llava' in lower or 'scout' in lower or 'maverick' in lower:
        caps.append('vision')
    if 'tool' in lower or any(f in lower for f in ('llama-3', 'mixtral', 'gemma', 'qwen')):
        caps.append('tools')
    if any(f in lower for f in ('llama-3', 'mixtral', 'qwen')):
        caps.append('json')
    if 'deepseek' in lower and 'r1' in lower:
        caps.append('reasoning')
    return _dedup(caps)


async def discover_groq() -> list[dict]:
    key = _require_key('groq', 'GROQ_API_KEY')
    data = await _get_json(
        'https://api.groq.com/openai/v1/models',
        headers={'Authorization': f'Bearer {key}'},
        provider_label='Groq',
    )
    models = [
        m for m in (data.get('data') or [])
        if not any(p in m.get('id', '').lower() for p in _GROQ_EXCLUDE)
        and m.get('active', True)
    ]
    models.sort(key=lambda m: m.get('id', ''))
    return [
        _entry(
            f"groq/{m.get('id', '')}",
            m.get('id', ''),
            f"Groq · {_title_words(m.get('id', '').split('/')[-1])}",
            free=False,
            capabilities=_groq_capabilities(m.get('id', '')),
        )
        for m in models
    ]


# ----------------------------------------------------------------------- openrouter

def _openrouter_is_free(model: dict) -> bool:
    pricing = model.get('pricing') or {}
    try:
        return (
            float(pricing.get('prompt', 0) or 0) == 0.0
            and float(pricing.get('completion', 0) or 0) == 0.0
        )
    except (ValueError, TypeError):
        return False


def _openrouter_capabilities(model: dict) -> list[str]:
    arch = model.get('architecture') or {}
    params = model.get('supported_parameters') or []
    input_modalities = arch.get('input_modalities') or []
    output_modalities = arch.get('output_modalities') or []
    modality = arch.get('modality', '')
    caps = []
    if 'text' in input_modalities and 'text' in output_modalities:
        caps.append('chat')
    if 'image' in input_modalities or 'image' in modality.lower():
        caps.append('vision')
    if 'audio' in input_modalities:
        caps.append('audio')
    if 'tools' in params or 'tool_choice' in params:
        caps.append('tools')
    if 'response_format' in params or 'structured_outputs' in params:
        caps.append('json')
    if 'reasoning' in params or 'thinking' in params:
        caps.append('reasoning')
    if 'image' in output_modalities:
        caps.append('image_generation')
    return _dedup(caps)


async def discover_openrouter() -> list[dict]:
    key = _require_key('openrouter', 'OPENROUTER_API_KEY')
    data = await _get_json(
        'https://openrouter.ai/api/v1/models',
        headers={
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/json',
            'User-Agent': 'spice-sibyl-openrouter-discovery/1.0',
        },
        provider_label='OpenRouter',
    )
    chat_models = [
        m for m in (data.get('data') or [])
        if 'text' in ((m.get('architecture') or {}).get('input_modalities') or [])
        and 'text' in ((m.get('architecture') or {}).get('output_modalities') or [])
    ]
    chat_models.sort(key=lambda m: m.get('id', ''))
    results = [
        _entry('openrouter/openrouter/free', 'openrouter/free', 'OpenRouter · Free',
               free=True, capabilities=['chat'])
    ]
    results.extend(
        _entry(
            f"openrouter/{m.get('id', '')}",
            m.get('id', ''),
            f"OpenRouter · {m.get('name', m.get('id', '')).replace(':', ' · ')}",
            free=_openrouter_is_free(m),
            capabilities=_openrouter_capabilities(m),
        )
        for m in chat_models
    )
    return results


# --------------------------------------------------------------------------- gemini

_GEMINI_FREE_PATTERNS = ('flash', 'gemini-1.0-pro', 'gemini-pro')


def _gemini_capabilities(model: dict) -> list[str]:
    name = model.get('name', '').removeprefix('models/').lower()
    methods = model.get('supportedGenerationMethods') or []
    caps = []
    if 'generatecontent' in [m.lower() for m in methods]:
        caps.append('chat')
    if 'vision' in name or any(f'gemini-{v}' in name for v in ('1.5', '2.0', '2.5')):
        caps.append('vision')
    if '1.5-pro' in name or '2.0' in name or '2.5' in name:
        caps.append('audio')
    if any(f'gemini-{v}' in name for v in ('1.5', '2.0', '2.5')):
        caps.append('tools')
        caps.append('json')
    if 'thinking' in name or 'reasoning' in name:
        caps.append('reasoning')
    return _dedup(caps)


async def discover_gemini() -> list[dict]:
    key = _require_key('gemini', 'GEMINI_API_KEY')
    data = await _get_json(
        'https://generativelanguage.googleapis.com/v1beta/models',
        params={'key': key},
        provider_label='Gemini',
    )
    text_models = [
        m for m in (data.get('models') or [])
        if 'generateContent' in (m.get('supportedGenerationMethods') or [])
    ]
    text_models.sort(key=lambda m: m.get('name', ''))
    results = []
    for m in text_models:
        short = m.get('name', '').removeprefix('models/')
        label = ' '.join(w.capitalize() for w in short.replace('-', ' ').split())
        results.append(
            _entry(
                f'gemini/{short}',
                short,
                f'Gemini · {label}',
                free=any(p in m.get('name', '').lower() for p in _GEMINI_FREE_PATTERNS),
                capabilities=_gemini_capabilities(m),
            )
        )
    return results


# ----------------------------------------------------------------------- cloudflare

def _cloudflare_label(name: str) -> str:
    s = re.sub(r'^@(?:cf|hf)/', '', name)
    model = s.split('/')[-1] if s else s
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


def _cloudflare_is_free(model: dict) -> bool:
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


def _cloudflare_capabilities(model: dict) -> list[str]:
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
    return _dedup(caps)


async def discover_cloudflare() -> list[dict]:
    key = _require_key('cloudflare', 'CLOUDFLARE_API_KEY')
    account_id = settings.cloudflare_account_id
    if not account_id:
        raise DiscoveryError(
            'CLOUDFLARE_ACCOUNT_ID is not configured in the backend .env.', status_code=400
        )
    data = await _get_json(
        f'https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/models/search',
        headers={'Authorization': f'Bearer {key}'},
        provider_label='Cloudflare',
    )
    text_models = [
        m for m in (data.get('result') or [])
        if ((m.get('task') or {}).get('name') == 'Text Generation')
    ]
    text_models.sort(key=lambda m: m.get('name', ''))
    return [
        _entry(
            f"cloudflare/{m.get('name', '')}",
            m.get('name', ''),
            _cloudflare_label(m.get('name', '')),
            free=_cloudflare_is_free(m),
            capabilities=_cloudflare_capabilities(m),
        )
        for m in text_models
    ]


# -------------------------------------------------------------------------- mistral

def _mistral_is_chat(model: dict) -> bool:
    caps = model.get('capabilities') or {}
    model_type = model.get('type', 'base')
    return (
        caps.get('completion_chat', False)
        and model_type in ('base', 'chat')
        and not model.get('id', '').startswith('ft:')
    )


def _mistral_capabilities(model: dict) -> list[str]:
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
    return _dedup(caps)


async def discover_mistral() -> list[dict]:
    key = _require_key('mistral', 'MISTRAL_API_KEY')
    data = await _get_json(
        'https://api.mistral.ai/v1/models',
        headers={'Authorization': f'Bearer {key}'},
        provider_label='Mistral',
    )
    chat_models = [m for m in (data.get('data') or []) if _mistral_is_chat(m)]
    chat_models.sort(key=lambda m: m.get('id', ''))
    return [
        _entry(
            f"mistral/{m.get('id', '')}",
            m.get('id', ''),
            f"Mistral · {' '.join(w.capitalize() for w in m.get('id', '').split('/')[-1].replace('-', ' ').split())}",
            free=False,
            capabilities=_mistral_capabilities(m),
        )
        for m in chat_models
    ]


# ------------------------------------------------------------------------- cerebras

def _cerebras_capabilities(model_id: str) -> list[str]:
    lower = model_id.lower()
    caps = ['chat']
    if 'scout' in lower or 'maverick' in lower or 'vision' in lower:
        caps.append('vision')
    # All current Cerebras models support tool use and JSON mode
    caps.append('tools')
    caps.append('json')
    if 'r1' in lower or 'reasoning' in lower or 'thinking' in lower:
        caps.append('reasoning')
    return _dedup(caps)


async def discover_cerebras() -> list[dict]:
    key = _require_key('cerebras', 'CEREBRAS_API_KEY')
    data = await _get_json(
        'https://api.cerebras.ai/v1/models',
        headers={'Authorization': f'Bearer {key}'},
        provider_label='Cerebras',
    )
    models = sorted(data.get('data') or [], key=lambda m: m.get('id', ''))
    return [
        _entry(
            f"cerebras/{m.get('id', '')}",
            m.get('id', ''),
            f"Cerebras · {_title_words(m.get('id', '').split('/')[-1])}",
            free=False,
            capabilities=_cerebras_capabilities(m.get('id', '')),
        )
        for m in models
    ]


# --------------------------------------------------------------------------- nvidia

_NVIDIA_SKIP_TYPES = frozenset({'embedding', 'reranking', 'vlm-only', 'audio', 'image'})


def _nvidia_is_chat(model: dict) -> bool:
    model_type = (model.get('model_type') or '').lower()
    if model_type in _NVIDIA_SKIP_TYPES:
        return False
    model_id = (model.get('id') or '').lower()
    if any(k in model_id for k in ('embed', 'rerank', 'whisper', 'tts', 'diffusion', 'stable')):
        return False
    return True


def _nvidia_capabilities(model_id: str) -> list[str]:
    lower = model_id.lower()
    caps = ['chat']
    if any(k in lower for k in ('vision', 'vl', 'vlm', 'llava', 'visual')):
        caps.append('vision')
    if any(k in lower for k in ('code', 'coder', 'codegen', 'starcoder')):
        caps.append('code')
    if any(k in lower for k in ('nemotron', 'r1', 'reasoning', 'think')):
        caps.append('reasoning')
    # Most NVIDIA NIM chat models support tool use
    caps.append('tools')
    return _dedup(caps)


async def discover_nvidia() -> list[dict]:
    key = _require_key('nvidia', 'NVIDIA_API_KEY')
    data = await _get_json(
        'https://integrate.api.nvidia.com/v1/models',
        headers={'Authorization': f'Bearer {key}'},
        provider_label='NVIDIA',
    )
    chat_models = [m for m in (data.get('data') or []) if _nvidia_is_chat(m)]
    chat_models.sort(key=lambda m: m.get('id', ''))
    return [
        _entry(
            f"nvidia/{m.get('id', '')}",
            m.get('id', ''),
            f"NVIDIA · {_title_words(m.get('id', '').split('/')[-1])}",
            free=False,
            capabilities=_nvidia_capabilities(m.get('id', '')),
        )
        for m in chat_models
    ]


# ---------------------------------------------------------------------------- agent

async def discover_agent() -> list[dict]:
    """List models from the Multi-MCP orchestrator sidecar, if reachable.

    The sidecar is OpenAI-compatible; if it does not expose /models (or is
    down) the registry's static_models fallback is used by the endpoint.
    """
    base = settings.orchestrator_base_url
    if not base:
        raise DiscoveryError(
            'ORCHESTRATOR_BASE_URL is not configured — the Multi-MCP sidecar is unavailable.',
            status_code=400,
        )
    data = await _get_json(f"{base.rstrip('/')}/models", timeout=10.0, provider_label='Agent sidecar')
    return [
        _entry(
            m.get('id', ''),
            m.get('id', '').removeprefix('agent/'),
            f"Agent · {_title_words(m.get('id', '').removeprefix('agent/'))}",
            free=True,
            capabilities=['chat', 'tools', 'agent'],
        )
        for m in (data.get('data') or [])
        if m.get('id')
    ]
