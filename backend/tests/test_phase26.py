"""
Phase 26 tests — semantic response cache (extends the Phase 19 exact-match cache).

On an exact-match miss the normalized last user message is embedded and compared
(cosine) against stored embeddings of recent entries in the same
(model, temperature, max_tokens) bucket; a match at/above the threshold replays
the saved reply flagged `cached_semantic`.
"""

import asyncio

from app.core.config import settings
from app.schemas.chat import ChatCompletionRequest, ChatMessage
from app.services import cache_service


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _req(text: str, model: str = "mock/m", temperature: float = 0.0) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content=text)],
        temperature=temperature,
    )


class _StubEmbedder:
    """Deterministic embeddings keyed by exact text — close texts must be mapped
    to close vectors by the test itself."""

    def __init__(self, table: dict[str, list[float]], model_id: str = "stub:e"):
        self.table = table
        self.model_id = model_id
        self.calls = 0

    async def embed_texts(self, texts):
        self.calls += 1
        return [self.table[t] for t in texts], self.model_id


def _install(monkeypatch, embedder, *, enabled=True, threshold=0.9):
    monkeypatch.setattr(cache_service.embedding_service, "embed_texts", embedder.embed_texts)
    monkeypatch.setattr(settings, "semantic_cache_enabled", enabled)
    monkeypatch.setattr(settings, "semantic_cache_threshold", threshold)


# ── cosine helper ────────────────────────────────────────────────────────────

def test_cosine_basic():
    assert cache_service._cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cache_service._cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cache_service._cosine([1.0, 0.0], [1.0]) == -1.0  # dim mismatch
    assert cache_service._cosine([0.0, 0.0], [1.0, 0.0]) == -1.0  # zero vector


# ── semantic hit / miss ──────────────────────────────────────────────────────

def test_semantic_hit_on_paraphrase(monkeypatch):
    cache_service.clear()
    embedder = _StubEmbedder({
        "How do I reset my password?": [1.0, 0.0, 0.0],
        "How can I reset my password?": [0.99, 0.14, 0.0],  # near-parallel
    })
    _install(monkeypatch, embedder)

    stored = _req("How do I reset my password?")
    key = cache_service.cache_key(stored)
    # Seed the store as chat_service would after a miss.
    hit, emb, model_id, bucket = _run(cache_service.semantic_get(stored))
    assert hit is None  # empty cache
    cache_service.put(key, "Open Settings → Security.", {"usage": {}},
                      embedding=emb, embed_model=model_id, bucket=bucket)

    # A paraphrase in the same bucket hits.
    para = _req("How can I reset my password?")
    hit, _, _, _ = _run(cache_service.semantic_get(para))
    assert hit is not None and hit["content"] == "Open Settings → Security."
    assert cache_service.stats()["semantic_hits"] == 1
    cache_service.clear()


def test_semantic_miss_below_threshold(monkeypatch):
    cache_service.clear()
    embedder = _StubEmbedder({
        "How do I reset my password?": [1.0, 0.0, 0.0],
        "What is the capital of France?": [0.0, 1.0, 0.0],  # orthogonal
    })
    _install(monkeypatch, embedder)

    stored = _req("How do I reset my password?")
    key = cache_service.cache_key(stored)
    _, emb, model_id, bucket = _run(cache_service.semantic_get(stored))
    cache_service.put(key, "answer", {"usage": {}},
                      embedding=emb, embed_model=model_id, bucket=bucket)

    unrelated = _req("What is the capital of France?")
    hit, _, _, _ = _run(cache_service.semantic_get(unrelated))
    assert hit is None
    cache_service.clear()


def test_semantic_respects_bucket(monkeypatch):
    """Same text + close vector but a different bucket (temperature) must miss."""
    cache_service.clear()
    embedder = _StubEmbedder({"same question": [1.0, 0.0]})
    _install(monkeypatch, embedder)

    stored = _req("same question", temperature=0.0)
    key = cache_service.cache_key(stored)
    _, emb, model_id, bucket = _run(cache_service.semantic_get(stored))
    cache_service.put(key, "a", {"usage": {}},
                      embedding=emb, embed_model=model_id, bucket=bucket)

    other_bucket = _req("same question", temperature=0.9)
    hit, _, _, _ = _run(cache_service.semantic_get(other_bucket))
    assert hit is None
    cache_service.clear()


def test_semantic_disabled(monkeypatch):
    cache_service.clear()
    embedder = _StubEmbedder({"q": [1.0]})
    _install(monkeypatch, embedder, enabled=False)
    hit, emb, model_id, bucket = _run(cache_service.semantic_get(_req("q")))
    assert hit is None and emb is None
    assert embedder.calls == 0  # never embeds when disabled
    cache_service.clear()


def test_semantic_degrades_when_provider_unreachable(monkeypatch):
    cache_service.clear()

    async def _boom(_texts):
        raise cache_service.embedding_service.EmbeddingError("no provider")

    monkeypatch.setattr(cache_service.embedding_service, "embed_texts", _boom)
    monkeypatch.setattr(settings, "semantic_cache_enabled", True)

    hit, emb, model_id, bucket = _run(cache_service.semantic_get(_req("q")))
    assert hit is None and emb is None
    assert bucket is not None  # bucket still computed for a potential exact put
    cache_service.clear()


def _seed(req, content, vec, model_id="stub:e"):
    """Store an entry directly with a known embedding (bypasses the embedder)."""
    cache_service.put(
        cache_service.cache_key(req), content, {"usage": {}},
        embedding=vec, embed_model=model_id, bucket=cache_service._bucket_key(req),
    )


# ── 26.b: scan bound + observability ─────────────────────────────────────────

def test_semantic_max_entries_bounds_scan(monkeypatch):
    embedder = _StubEmbedder({"reset password?": [0.99, 0.14, 0.0]})
    _install(monkeypatch, embedder)

    # Older entry is the close match; a newer unrelated entry follows it.
    cache_service.clear()
    _seed(_req("how to reset password"), "match", [1.0, 0.0, 0.0])
    _seed(_req("capital of france"), "noise", [0.0, 1.0, 0.0])

    # Window of 1 only sees the newest (unrelated) entry → miss.
    monkeypatch.setattr(settings, "semantic_cache_max_entries", 1)
    hit, _, _, _ = _run(cache_service.semantic_get(_req("reset password?")))
    assert hit is None

    # Widening the window reaches the older matching entry → hit.
    monkeypatch.setattr(settings, "semantic_cache_max_entries", 10)
    hit, _, _, _ = _run(cache_service.semantic_get(_req("reset password?")))
    assert hit is not None and hit["content"] == "match"
    cache_service.clear()


def test_stats_tracks_exact_and_semantic(monkeypatch):
    embedder = _StubEmbedder({
        "q one": [1.0, 0.0],
        "q one again": [0.999, 0.045],  # near-parallel → semantic hit
        "totally different": [0.0, 1.0],  # semantic miss
    })
    _install(monkeypatch, embedder)
    cache_service.clear()

    # Exact hit/miss counters.
    req = _req("q one")
    key = cache_service.cache_key(req)
    assert cache_service.get(key) is None            # exact miss
    _seed(req, "answer", [1.0, 0.0])
    assert cache_service.get(key) is not None         # exact hit

    # Semantic hit then semantic miss.
    _run(cache_service.semantic_get(_req("q one again")))
    _run(cache_service.semantic_get(_req("totally different")))

    s = cache_service.stats()
    assert s["hits"] == 1 and s["misses"] == 1
    assert s["semantic_hits"] == 1 and s["semantic_misses"] == 1
    cache_service.clear()
    assert cache_service.stats()["semantic_misses"] == 0  # clear() resets
    cache_service.clear()


def test_semantic_skips_uncacheable(monkeypatch):
    """Tools / agent requests never enter the semantic layer (bucket is None)."""
    cache_service.clear()
    embedder = _StubEmbedder({"x": [1.0]})
    _install(monkeypatch, embedder)

    agent = ChatCompletionRequest(
        model="agent/multi", messages=[ChatMessage(role="user", content="x")]
    )
    hit, _, _, bucket = _run(cache_service.semantic_get(agent))
    assert hit is None and bucket is None
    assert embedder.calls == 0
    cache_service.clear()
