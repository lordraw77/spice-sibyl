"""
cache_service — Phase 19 response cache + Phase 26 semantic layer.

Exact-match, in-memory (per-process) LRU cache of completed chat replies.
A hit skips the provider call entirely: the cached final content + telemetry
meta are replayed (as a single chunk on streaming requests, flagged cached).

Phase 26 adds a semantic layer (SEMANTIC_CACHE_ENABLED): on an exact-match miss
the normalized last user message is embedded and compared (cosine) against the
stored embeddings of recent entries in the *same* (model, temperature,
max_tokens) bucket; a match at/above SEMANTIC_CACHE_THRESHOLD replays the saved
reply flagged `cached_semantic`. Embeddings are stored alongside content/meta
and the existing LRU + TTL are reused. Degrades silently to exact-match-only
when no embedding provider is reachable.

Requests with tools, agent/* models or an in-flight image payload are never
cached. The key covers model, normalized messages, temperature and max_tokens
so any prompt change misses.
"""

import hashlib
import json
import logging
import math
import time
from collections import OrderedDict

from app.core.config import settings
from app.schemas.chat import ChatCompletionRequest
from app.services import embedding_service

logger = logging.getLogger(__name__)

# key → (expires_at, payload) where payload = {"content": str, "meta": dict} and,
# for semantically-indexed entries, "_bucket"/"_embedding"/"_embed_model".
_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()

_hits = 0
_misses = 0
_semantic_hits = 0
_semantic_misses = 0


def _normalize_content(content) -> str:
    if isinstance(content, str):
        return content
    # Multimodal payloads (images) are intentionally not cached.
    return ""


def _bucket_key(request: ChatCompletionRequest) -> str | None:
    """Group key over (model, temperature, max_tokens); None when uncacheable."""
    if cache_key(request) is None:
        return None
    raw = json.dumps(
        [request.model, request.temperature, request.max_tokens],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _last_user_text(request: ChatCompletionRequest) -> str:
    for msg in reversed(request.messages):
        if msg.role == "user":
            return _normalize_content(msg.content)
    return ""


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return -1.0
    return dot / (na * nb)


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


def put(
    key: str | None,
    content: str,
    meta: dict | None = None,
    *,
    embedding: list[float] | None = None,
    embed_model: str | None = None,
    bucket: str | None = None,
) -> None:
    if key is None or not content:
        return
    payload: dict = {"content": content, "meta": meta or {}}
    # Index for the semantic layer only when we have a usable embedding.
    if embedding and bucket:
        payload["_embedding"] = embedding
        payload["_embed_model"] = embed_model or ""
        payload["_bucket"] = bucket
    _cache[key] = (time.monotonic() + settings.response_cache_ttl_seconds, payload)
    _cache.move_to_end(key)
    while len(_cache) > settings.response_cache_max_entries:
        _cache.popitem(last=False)


async def semantic_get(
    request: ChatCompletionRequest,
) -> tuple[dict | None, list[float] | None, str | None, str | None]:
    """Find a semantically-close cached reply for an exact-match miss.

    Returns (hit_payload, query_embedding, embed_model, bucket): hit_payload is
    the best cached payload at/above SEMANTIC_CACHE_THRESHOLD in the request's
    (model, temperature, max_tokens) bucket, or None. query_embedding/embed_model
    are returned so the caller can persist them on the ensuing put() without a
    second embedding call. Degrades to (None, None, None, bucket) silently when
    disabled or no embedding provider is reachable.
    """
    bucket = _bucket_key(request)
    if not settings.semantic_cache_enabled or bucket is None:
        return None, None, None, bucket
    text = _last_user_text(request)
    if not text.strip():
        return None, None, None, bucket
    try:
        vectors, model_id = await embedding_service.embed_texts([text])
    except Exception as exc:  # noqa: BLE001 — degrade to exact-match-only
        logger.debug("Semantic cache: embedding unavailable (%s); exact-match only", exc)
        return None, None, None, bucket
    if not vectors:
        return None, None, None, bucket
    query = vectors[0]

    best_key: str | None = None
    best_payload: dict | None = None
    best_score = settings.semantic_cache_threshold
    now = time.monotonic()
    # Only scan the most-recent entries (OrderedDict keeps LRU order, newest last)
    # so cosine cost stays bounded regardless of the overall cache size.
    items = list(_cache.items())
    if settings.semantic_cache_max_entries > 0:
        items = items[-settings.semantic_cache_max_entries:]
    for key, (expires_at, payload) in items:
        if expires_at < now:
            continue
        if payload.get("_bucket") != bucket or payload.get("_embed_model") != model_id:
            continue
        emb = payload.get("_embedding")
        if not emb:
            continue
        score = _cosine(query, emb)
        if score >= best_score:
            best_score, best_key, best_payload = score, key, payload

    global _semantic_hits, _semantic_misses
    if best_payload is not None and best_key is not None:
        _semantic_hits += 1
        _cache.move_to_end(best_key)
        logger.info("Semantic cache hit (score=%.3f) for model=%s", best_score, request.model)
    else:
        _semantic_misses += 1
    return best_payload, query, model_id, bucket


def stats() -> dict:
    """Cache observability: exact-match vs semantic hit/miss counts.

    `hits`/`misses` are exact-match (19.c) lookups; `semantic_hits`/
    `semantic_misses` count the fuzzy layer (26.a) tried after an exact miss.
    """
    return {
        "entries": len(_cache),
        "hits": _hits,
        "misses": _misses,
        "semantic_hits": _semantic_hits,
        "semantic_misses": _semantic_misses,
    }


def clear() -> None:
    global _semantic_hits, _semantic_misses
    _cache.clear()
    _semantic_hits = 0
    _semantic_misses = 0
