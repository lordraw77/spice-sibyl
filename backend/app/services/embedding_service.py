"""
embedding_service — provider-agnostic text embedding with a fallback chain.

Mirrors the image-generation chain style: settings.embedding_chain is a
comma-separated list of "provider:model" entries tried in order, skipping
unconfigured providers and falling back on error.  Supported providers:
ollama (local, default), gemini, mistral.

embed_texts() returns (vectors, model_id) where model_id identifies the entry
that produced the vectors — stored alongside chunks so retrieval can embed the
query with the same model.
"""

import logging

import httpx

from app.core.config import settings
from app.services import key_resolver

logger = logging.getLogger(__name__)

_TIMEOUT = 120.0


class EmbeddingError(RuntimeError):
    """Raised when no embedding provider in the chain can produce vectors."""


def _parse_chain() -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for raw in settings.embedding_chain.split(","):
        raw = raw.strip()
        if not raw or ":" not in raw:
            continue
        provider, _, model = raw.partition(":")
        entries.append((provider.strip(), model.strip()))
    return entries


async def _embed_ollama(model: str, texts: list[str]) -> list[list[float]]:
    base = settings.ollama_api_base.rstrip("/")
    vectors: list[list[float]] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for text in texts:
            resp = await client.post(
                f"{base}/api/embeddings",
                json={"model": model, "prompt": text},
            )
            resp.raise_for_status()
            vectors.append(resp.json()["embedding"])
    return vectors


async def _embed_gemini(model: str, texts: list[str]) -> list[list[float]]:
    key = key_resolver.resolve("gemini")
    if not key:
        raise EmbeddingError("gemini not configured")
    name = model if model.startswith("models/") else f"models/{model}"
    requests = [
        {"model": name, "content": {"parts": [{"text": t}]}} for t in texts
    ]
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/{name}:batchEmbedContents"
        f"?key={key}"
    )
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, json={"requests": requests})
        resp.raise_for_status()
        data = resp.json()
    return [e["values"] for e in data["embeddings"]]


async def _embed_mistral(model: str, texts: list[str]) -> list[list[float]]:
    key = key_resolver.resolve("mistral")
    if not key:
        raise EmbeddingError("mistral not configured")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            "https://api.mistral.ai/v1/embeddings",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()
    # API may return data out of order — sort by index to be safe
    items = sorted(data["data"], key=lambda d: d.get("index", 0))
    return [item["embedding"] for item in items]


_HANDLERS = {
    "ollama": _embed_ollama,
    "gemini": _embed_gemini,
    "mistral": _embed_mistral,
}


async def embed_texts(texts: list[str]) -> tuple[list[list[float]], str]:
    """Embed a batch of texts, trying each chain entry until one succeeds.

    Returns (vectors, model_id) where model_id is "provider:model".
    Raises EmbeddingError if no provider in the chain works.
    """
    if not texts:
        return [], ""

    chain = _parse_chain()
    if not chain:
        raise EmbeddingError("embedding_chain is empty")

    last_error: Exception | None = None
    for provider, model in chain:
        handler = _HANDLERS.get(provider)
        if not handler:
            logger.warning("Unknown embedding provider in chain: %s", provider)
            continue
        try:
            vectors = await handler(model, texts)
            logger.info("Embedded %d text(s) via %s:%s (dim=%d)", len(texts), provider, model, len(vectors[0]) if vectors else 0)
            return vectors, f"{provider}:{model}"
        except Exception as exc:
            last_error = exc
            logger.warning("Embedding via %s:%s failed: %s", provider, model, exc)

    raise EmbeddingError(
        f"No embedding provider available (chain: {settings.embedding_chain}). "
        f"Last error: {last_error}"
    )
