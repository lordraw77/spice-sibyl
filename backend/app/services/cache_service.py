"""
cache_service — Phase 19 response cache.

Exact-match, in-memory (per-process) LRU cache of completed chat replies.
A hit skips the provider call entirely: the cached final content + telemetry
meta are replayed (as a single chunk on streaming requests, flagged cached).

Requests with tools, agent/* models or an in-flight image payload are never
cached. The key covers model, normalized messages, temperature and max_tokens
so any prompt change misses.
"""

import hashlib
import json
import time
from collections import OrderedDict

from app.core.config import settings
from app.schemas.chat import ChatCompletionRequest

# key → (expires_at, payload) where payload = {"content": str, "meta": dict}
_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()

_hits = 0
_misses = 0


def _normalize_content(content) -> str:
    if isinstance(content, str):
        return content
    # Multimodal payloads (images) are intentionally not cached.
    return ""


def cache_key(request: ChatCompletionRequest) -> str | None:
    """Return the cache key for a request, or None when it must not be cached."""
    if not settings.response_cache_enabled:
        return None
    if request.tools or (request.model or "").startswith("agent/"):
        return None
    parts: list = [request.model, request.temperature, request.max_tokens]
    for msg in request.messages:
        if isinstance(msg.content, list):
            return None  # multimodal — skip
        parts.append((msg.role, _normalize_content(msg.content)))
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get(key: str | None) -> dict | None:
    """Return {"content", "meta"} on a fresh hit, else None."""
    global _hits, _misses
    if key is None:
        return None
    entry = _cache.get(key)
    if entry is None:
        _misses += 1
        return None
    expires_at, payload = entry
    if expires_at < time.monotonic():
        _cache.pop(key, None)
        _misses += 1
        return None
    _cache.move_to_end(key)
    _hits += 1
    return payload


def put(key: str | None, content: str, meta: dict | None = None) -> None:
    if key is None or not content:
        return
    _cache[key] = (
        time.monotonic() + settings.response_cache_ttl_seconds,
        {"content": content, "meta": meta or {}},
    )
    _cache.move_to_end(key)
    while len(_cache) > settings.response_cache_max_entries:
        _cache.popitem(last=False)


def stats() -> dict:
    return {"entries": len(_cache), "hits": _hits, "misses": _misses}


def clear() -> None:
    _cache.clear()
