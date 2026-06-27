"""
rag_service — document ingestion and retrieval for the knowledge base.

Pipeline: extract text → chunk (with overlap, tracking character offsets) → embed
→ store vectors as float32 BLOBs in kb_chunks (plus a lexical copy in kb_chunks_fts).

Retrieval (Phase 17) is *hybrid*: an FTS5 lexical arm and a vector-similarity arm
are fused with Reciprocal Rank Fusion (RRF), then optionally reranked by an LLM
before the top-k chunks are injected as context. Pure-vector retrieval remains the
fallback when hybrid is disabled or the lexical arm is empty. The vector scan is
still a per-profile numpy cosine pass (sqlite-vec remains the documented upgrade
path for very large corpora).
"""

import io
import logging
import re

import aiosqlite
import httpx
import numpy as np

from app.core.config import settings
from app.db import kb_repository as repo
from app.schemas.knowledge import RagSource
from app.services import embedding_service

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 800       # target characters per chunk
_CHUNK_OVERLAP = 120    # characters shared between adjacent chunks
_RRF_K = 60             # Reciprocal Rank Fusion damping constant


# ── Text extraction ────────────────────────────────────────────
def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        from PyPDF2 import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(p.extract_text() or "" for p in reader.pages).strip()
    if name.endswith(".docx"):
        from docx import Document

        doc = Document(io.BytesIO(data))
        return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip()).strip()
    if name.endswith((".txt", ".md", ".markdown")):
        return data.decode("utf-8", errors="replace").strip()
    raise ValueError(f"Unsupported file type: {filename}")


async def fetch_url_text(url: str, max_chars: int = 200_000) -> str:
    """Fetch a web page and return its plain text (HTML stripped), for KB ingestion.

    Unlike the read_url tool this keeps the full document (bounded only by a high
    safety cap) so the whole page is chunked and indexed.
    """
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise ValueError("Only http(s) URLs can be ingested.")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "text" not in content_type and "json" not in content_type:
            raise ValueError(f"Cannot ingest binary content (content-type: {content_type})")
        text = resp.text

    # Drop script/style blocks, then remaining tags; decode common entities.
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#x27;", "'").replace("&nbsp;", " "))
    text = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


# ── Chunking ───────────────────────────────────────────────────
def chunk_text(text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Backward-compatible chunker returning chunk strings only."""
    return [c for c, _s, _e in chunk_text_with_offsets(text, size, overlap)]


def chunk_text_with_offsets(
    text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP
) -> list[tuple[str, int, int]]:
    """Chunk text, returning (content, char_start, char_end) spans.

    Offsets index into the *original* text so the UI can highlight the exact
    passage. content is the stripped chunk (leading/trailing whitespace trimmed),
    while the offsets bound the underlying slice.
    """
    if not text or not text.strip():
        return []
    if len(text) <= size:
        return [(text.strip(), 0, len(text))]

    step = max(size - overlap, 1)
    chunks: list[tuple[str, int, int]] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        raw = text[start:end]
        content = raw.strip()
        if content:
            chunks.append((content, start, end))
        if end >= len(text):
            break
        start += step
    return chunks


# ── Vector helpers ─────────────────────────────────────────────
def _to_blob(vector: list[float]) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def _from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


# ── Ingestion ──────────────────────────────────────────────────
async def ingest(
    db: aiosqlite.Connection, doc_id: str, profile_id: str, filename: str, data: bytes
) -> int:
    """Extract, chunk, embed and persist an uploaded file. Returns the chunk count."""
    try:
        text = extract_text(filename, data)
        return await ingest_text(db, doc_id, profile_id, filename, text)
    except Exception as exc:
        await repo.mark_error(db, doc_id, str(exc))
        raise


async def ingest_text(
    db: aiosqlite.Connection, doc_id: str, profile_id: str, label: str, text: str
) -> int:
    """Chunk, embed and persist already-extracted text. Returns the chunk count.

    Shared by file uploads and URL ingestion. Stores the full source_text so the
    document can be re-embedded later and citations can deep-link to passages.
    On failure the document is marked status='error' and the exception re-raised.
    """
    try:
        spans = chunk_text_with_offsets(text)
        if not spans:
            raise ValueError("No extractable text found in document.")

        await repo.set_source_text(db, doc_id, text)
        contents = [c for c, _s, _e in spans]
        vectors, embed_model = await embedding_service.embed_texts(contents)
        rows = [
            (idx, content, start, end, _to_blob(vec))
            for idx, ((content, start, end), vec) in enumerate(zip(spans, vectors))
        ]
        await repo.insert_chunks(db, doc_id, profile_id, rows, embed_model)
        await repo.mark_ready(db, doc_id, len(rows))
        logger.info("Ingested '%s' → %d chunks via %s", label, len(rows), embed_model)
        return len(rows)
    except Exception as exc:
        await repo.mark_error(db, doc_id, str(exc))
        raise


async def reembed(db: aiosqlite.Connection, doc_id: str, profile_id: str) -> int:
    """Re-chunk and re-embed a document from its stored source_text.

    Used after changing EMBEDDING_CHAIN. Returns the new chunk count.
    """
    source = await repo.get_document_source(db, doc_id)
    if not source or not source.source_text:
        raise ValueError("Document has no stored source text to re-embed. Re-upload it.")
    await repo.clear_chunks(db, doc_id)
    return await ingest_text(db, doc_id, profile_id, source.filename, source.source_text)


# ── Retrieval ──────────────────────────────────────────────────
def _fts_query(q: str) -> str:
    """Turn a user query into an FTS5 prefix-match OR expression."""
    words = re.sub(r"[^\w\s]", " ", q).split()
    if not words:
        return '""'
    return " OR ".join(f'"{w}"*' for w in words)


def _row_to_source(row: aiosqlite.Row, score: float) -> RagSource:
    return RagSource(
        document_id=row["document_id"],
        filename=row["filename"],
        chunk_index=row["chunk_index"],
        score=round(float(score), 4),
        snippet=row["content"],
        char_start=row["char_start"] if "char_start" in row.keys() else 0,
        char_end=row["char_end"] if "char_end" in row.keys() else 0,
    )


async def _vector_candidates(
    db: aiosqlite.Connection,
    profile_id: str,
    query: str,
    pool: int,
    min_score: float,
    document_ids: list[str] | None,
) -> list[tuple[str, float, aiosqlite.Row]]:
    """Return (chunk_id, cosine_score, row) ranked desc, best `pool` kept."""
    rows = await repo.iter_chunk_vectors(db, profile_id, document_ids)
    if not rows:
        return []
    query_vecs, embed_model = await embedding_service.embed_texts([query])
    q = np.asarray(query_vecs[0], dtype=np.float32)
    q_norm = np.linalg.norm(q)
    if q_norm == 0:
        logger.warning("RAG retrieve: zero-norm query embedding (model=%s)", embed_model)
        return []

    scored: list[tuple[str, float, aiosqlite.Row]] = []
    skipped_dim = 0
    for row in rows:
        vec = _from_blob(row["embedding"])
        if vec.shape != q.shape:
            skipped_dim += 1
            continue
        denom = q_norm * np.linalg.norm(vec)
        if denom == 0:
            continue
        score = float(np.dot(q, vec) / denom)
        if score >= min_score:
            scored.append((row["id"], score, row))

    if skipped_dim:
        logger.warning(
            "RAG retrieve: %d chunk(s) skipped due to embedding dimension mismatch — "
            "re-embed documents after changing the embedding model", skipped_dim,
        )
    scored.sort(key=lambda s: s[1], reverse=True)
    return scored[:pool]


async def _lexical_candidates(
    db: aiosqlite.Connection,
    profile_id: str,
    query: str,
    pool: int,
    document_ids: list[str] | None,
) -> list[tuple[str, aiosqlite.Row]]:
    """Return (chunk_id, row) ranked best-first by FTS5 bm25."""
    try:
        rows = await repo.search_chunks_fts(
            db, profile_id, _fts_query(query), limit=pool, document_ids=document_ids
        )
    except Exception as exc:  # malformed FTS expression, etc. — degrade gracefully
        logger.warning("RAG lexical arm failed: %s", exc)
        return []
    return [(r["id"], r) for r in rows]


async def retrieve(
    db: aiosqlite.Connection,
    profile_id: str,
    query: str,
    top_k: int = 4,
    min_score: float = 0.2,
    document_ids: list[str] | None = None,
) -> list[RagSource]:
    """Hybrid retrieval (lexical + vector via RRF) with optional LLM rerank.

    Falls back to pure-vector ranking when hybrid is disabled.
    """
    pool = max(settings.rag_candidate_pool, top_k)

    vector = await _vector_candidates(db, profile_id, query, pool, min_score, document_ids)

    if not settings.rag_hybrid:
        results = [_row_to_source(row, score) for _cid, score, row in vector[:top_k]]
        logger.info(
            "RAG retrieve (vector): profile=%s matched=%d top_k=%d query=%r",
            profile_id, len(vector), len(results), query[:80],
        )
        return await _maybe_rerank(query, results, top_k)

    lexical = await _lexical_candidates(db, profile_id, query, pool, document_ids)

    # Reciprocal Rank Fusion across the two arms.
    fused: dict[str, float] = {}
    rows_by_id: dict[str, aiosqlite.Row] = {}
    for rank, (cid, _score, row) in enumerate(vector):
        fused[cid] = fused.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        rows_by_id[cid] = row
    for rank, (cid, row) in enumerate(lexical):
        fused[cid] = fused.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        rows_by_id.setdefault(cid, row)

    ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
    results = [_row_to_source(rows_by_id[cid], score) for cid, score in ranked[: max(pool, top_k)]]

    logger.info(
        "RAG retrieve (hybrid): profile=%s vector=%d lexical=%d fused=%d top_k=%d query=%r",
        profile_id, len(vector), len(lexical), len(ranked), top_k, query[:80],
    )
    return await _maybe_rerank(query, results, top_k)


# ── Reranking ──────────────────────────────────────────────────
async def _maybe_rerank(
    query: str, candidates: list[RagSource], top_k: int
) -> list[RagSource]:
    mode = (settings.rag_rerank or "").strip().lower()
    if mode not in ("llm",) or len(candidates) <= 1:
        return candidates[:top_k]
    try:
        return await _llm_rerank(query, candidates, top_k)
    except Exception as exc:
        logger.warning("RAG LLM rerank failed (%s); falling back to fused order", exc)
        return candidates[:top_k]


async def _llm_rerank(
    query: str, candidates: list[RagSource], top_k: int
) -> list[RagSource]:
    """Ask an LLM to order the candidate passages by relevance to the query.

    The model returns a JSON array of 1-based indices; unknown/duplicate indices
    are ignored and any missing candidates appended in their original order.
    """
    import json

    from app.schemas.chat import ChatCompletionRequest, ChatMessage
    from app.services.provider_factory import ProviderFactory

    listing = "\n".join(
        f"[{i + 1}] {c.snippet[:500]}" for i, c in enumerate(candidates)
    )
    prompt = (
        "You are a search reranker. Given a query and numbered passages, return the "
        "passage numbers ordered from most to least relevant as a JSON array of "
        "integers (e.g. [3,1,2]). Output ONLY the JSON array.\n\n"
        f"Query: {query}\n\nPassages:\n{listing}"
    )
    req = ChatCompletionRequest(
        model=settings.rag_rerank_model,
        messages=[ChatMessage(role="user", content=prompt)],
        temperature=0.0,
        max_tokens=120,
    )
    provider = ProviderFactory.get_provider(settings.rag_rerank_model)
    resp = await provider.complete(req)
    text = resp.choices[0].message.content or ""
    match = re.search(r"\[[\d,\s]+\]", text)
    if not match:
        raise ValueError(f"reranker returned no JSON array: {text[:120]!r}")
    order = json.loads(match.group(0))

    seen: set[int] = set()
    ranked: list[RagSource] = []
    for idx in order:
        i = int(idx) - 1
        if 0 <= i < len(candidates) and i not in seen:
            ranked.append(candidates[i])
            seen.add(i)
    for i, c in enumerate(candidates):
        if i not in seen:
            ranked.append(c)
    logger.info("RAG LLM rerank: reordered %d candidate(s) via %s", len(candidates), settings.rag_rerank_model)
    return ranked[:top_k]


def build_context_block(sources: list[RagSource]) -> str:
    """Render retrieved chunks into a system-prompt context block."""
    parts = [
        f"[{i + 1}] (source: {s.filename}#{s.chunk_index})\n{s.snippet}"
        for i, s in enumerate(sources)
    ]
    joined = "\n\n".join(parts)
    return (
        "Use the following retrieved context to answer the user's question. "
        "If the context is not relevant, answer normally and ignore it. "
        "Cite sources by their filename when you use them.\n\n"
        f"{joined}"
    )
