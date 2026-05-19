"""Runtime overrides for provider configuration.

Stored as JSON in the same directory as the YAML catalog (or /config/ if mounted).
Allows enabling/disabling providers and setting API keys without restarting the process.
"""

import json
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()

_DEFAULT_PATH = Path('/config/runtime_overrides.json')
_FALLBACK_PATH = Path(__file__).with_name('runtime_overrides.json')


def _path() -> Path:
    return _DEFAULT_PATH if _DEFAULT_PATH.parent.exists() else _FALLBACK_PATH


def load_overrides() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {}
    with _LOCK:
        with p.open('r', encoding='utf-8') as fh:
            return json.load(fh) or {}


def _save(data: dict[str, Any]) -> None:
    with _LOCK:
        with _path().open('w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2)


def get_provider_override(provider_id: str) -> dict[str, Any]:
    return load_overrides().get('providers', {}).get(provider_id, {})


def set_provider_override(provider_id: str, **kwargs: Any) -> None:
    data = load_overrides()
    entry = data.setdefault('providers', {}).setdefault(provider_id, {})
    entry.update(kwargs)
    _save(data)
