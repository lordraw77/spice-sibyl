"""Runtime overrides for provider configuration.

Allows enabling/disabling providers and setting API keys without restarting
the process. Written to the first writable location among /data (the rw
Docker volume — /config is root-owned and read-only for the container's app
user), /config, and the directory of this module; reads also consider the
other locations so overrides saved by older versions in /config are honored.
"""

import json
import os
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()

_CANDIDATE_DIRS = (Path('/data'), Path('/config'), Path(__file__).parent)
_FILENAME = 'runtime_overrides.json'


def _write_path() -> Path:
    for d in _CANDIDATE_DIRS:
        if d.is_dir() and os.access(d, os.W_OK):
            return d / _FILENAME
    return _CANDIDATE_DIRS[-1] / _FILENAME


def _read_path() -> Path | None:
    # Prefer the write location, then any legacy location holding the file
    candidates = [_write_path()] + [d / _FILENAME for d in _CANDIDATE_DIRS]
    for p in candidates:
        if p.exists():
            return p
    return None


def load_overrides() -> dict[str, Any]:
    p = _read_path()
    if p is None:
        return {}
    with _LOCK:
        with p.open('r', encoding='utf-8') as fh:
            return json.load(fh) or {}


def _save(data: dict[str, Any]) -> None:
    with _LOCK:
        with _write_path().open('w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2)


def get_provider_override(provider_id: str) -> dict[str, Any]:
    return load_overrides().get('providers', {}).get(provider_id, {})


def set_provider_override(provider_id: str, **kwargs: Any) -> None:
    data = load_overrides()
    entry = data.setdefault('providers', {}).setdefault(provider_id, {})
    entry.update(kwargs)
    _save(data)
