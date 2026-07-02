"""Persistent store for models discovered at runtime.

Discovery results (POST /v1/providers/{id}/discover) are saved here as JSON,
one entry per provider, and merged into the model catalog alongside the
static YAML. Stored in the first writable location among /data (the rw Docker
volume that also holds the SQLite DB — /config is root-owned and read-only
for the container's app user), /config, and the directory of this module.
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()

_CANDIDATE_DIRS = (Path('/data'), Path('/config'), Path(__file__).parent)
_FILENAME = 'discovered_models.json'


def _path() -> Path:
    for d in _CANDIDATE_DIRS:
        if d.is_dir() and os.access(d, os.W_OK):
            return d / _FILENAME
    return _CANDIDATE_DIRS[-1] / _FILENAME


def _load() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {}
    try:
        with p.open('r', encoding='utf-8') as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def save_provider_models(provider_id: str, models: list[dict[str, Any]]) -> None:
    """Replace the stored model list for a provider with a fresh discovery result."""
    with _LOCK:
        data = _load()
        data.setdefault('providers', {})[provider_id] = {
            'discovered_at': int(time.time()),
            'models': models,
        }
        with _path().open('w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2)


def get_provider_entry(provider_id: str) -> dict[str, Any]:
    """Return {'discovered_at', 'models'} for a provider, or {} if never discovered."""
    with _LOCK:
        return _load().get('providers', {}).get(provider_id, {})


def iter_discovered() -> dict[str, dict[str, Any]]:
    """Return the full provider_id → {'discovered_at', 'models'} mapping."""
    with _LOCK:
        return _load().get('providers', {})
