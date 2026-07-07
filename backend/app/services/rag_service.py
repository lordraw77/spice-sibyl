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

import logging
import re
from dataclasses import dataclass

import aiosqlite
import httpx
import numpy as np

from app.core.config import settings
from app.db import database
from app.db import kb_repository as repo
from app.schemas.knowledge import RagSource
from app.services import document_converter, embedding_service

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 800       # target characters per chunk
_CHUNK_OVERLAP = 120    # characters shared between adjacent chunks
_RRF_K = 60             # Reciprocal Rank Fusion damping constant


# ── Text extraction (wikillm: MarkItDown → structured Markdown) ─
def extract_text(filename: str, data: bytes) -> str:
    """Convert an uploaded file to Markdown via MarkItDown.

    Kept as a thin wrapper so existing callers keep working; the canonical
    entry point is document_converter.to_markdown. The returned Markdown is the
    source of truth for chunking, the wiki tree and the knowledge graph.
    """
    return document_converter.to_markdown(filename, data)


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

    # wikillm: convert the fetched HTML to structured Markdown (headings/tables/
    # lists) so URL ingestion feeds the same section-aware pipeline as uploads.
    markdown = document_converter.html_to_markdown(text)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    if len(markdown) > max_chars:
        markdown = markdown[:max_chars]
    return markdown


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


# ── Markdown structure (wikillm) ───────────────────────────────
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*\s*$")


@dataclass
class MarkdownSection:
    """A slice of Markdown under one heading (raw offsets into the document)."""
    level: int          # heading level; 0 = pre-heading preamble
    heading: str        # this section's heading text ("" for the preamble)
    path: list[str]     # breadcrumb of ancestor headings, incl. this one
    body_start: int     # offset where the body begins (after the heading line)
    body_end: int       # offset where the section ends (next heading / EOF)
    body: str           # raw body slice (NOT stripped — keeps offsets aligned)


def parse_markdown_sections(markdown: str) -> list[MarkdownSection]:
    """Split Markdown into heading-scoped sections with a breadcrumb path.

    Each section's body is the text directly beneath its heading up to the next
    heading of any level (child content lives in child sections). Offsets index
    the *original* markdown so callers can deep-link to passages. A leading
    preamble (text before the first heading) becomes a level-0 section.
    """
    if not markdown:
        return []
    headings: list[tuple[int, str, int, int]] = []  # (level, text, line_start, line_end)
    offset = 0
    for line in markdown.splitlines(keepends=True):
        m = _HEADING_RE.match(line.rstrip("\n"))
        if m:
            headings.append((len(m.group(1)), m.group(2).strip(), offset, offset + len(line)))
        offset += len(line)

    sections: list[MarkdownSection] = []
    first_start = headings[0][2] if headings else len(markdown)
    if markdown[:first_start].strip():
        sections.append(MarkdownSection(0, "", [], 0, first_start, markdown[:first_start]))

    stack: list[tuple[int, str]] = []
    for i, (level, text, _hstart, hend) in enumerate(headings):
        body_end = headings[i + 1][2] if i + 1 < len(headings) else len(markdown)
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))
        sections.append(
            MarkdownSection(level, text, [h for _l, h in stack], hend, body_end, markdown[hend:body_end])
        )
    return sections


def chunk_markdown_with_offsets(
    markdown: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP
) -> list[tuple[str, int, int, str, str]]:
    """Structure-aware chunking: chunk *within* Markdown sections.

    Returns (content, char_start, char_end, section_path, heading). Falls back to
    a single flat pass (empty section_path/heading) when the document has no
    headings, so plain-text sources still ingest.
    """
    sections = parse_markdown_sections(markdown)
    out: list[tuple[str, int, int, str, str]] = []
    for sec in sections:
        if not sec.body.strip():
            continue
        crumb = " > ".join(sec.path)
        for content, ls, le in chunk_text_with_offsets(sec.body, size, overlap):
            out.append((content, sec.body_start + ls, sec.body_start + le, crumb, sec.heading))
    if not out:
        return [(c, s, e, "", "") for c, s, e in chunk_text_with_offsets(markdown, size, overlap)]
    return out


# ── Vector helpers ─────────────────────────────────────────────
def _to_blob(vector: list[float]) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def _from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


# ── Ingestion ──────────────────────────────────────────────────
async def ingest(
    db: aiosqlite.Connection, doc_id: str, profile_id: str, filename: str, data: bytes
) -> int:
    """Convert (MarkItDown), chunk, embed and persist an uploaded file.

    Returns the chunk count. The converted Markdown becomes the document's
    canonical source, driving both the wiki tree and the knowledge graph.
    """
    try:
        markdown = document_converter.to_markdown(filename, data)
        return await ingest_text(db, doc_id, profile_id, filename, markdown)
    except Exception as exc:
        await repo.mark_error(db, doc_id, str(exc))
        raise


async def ingest_text(
    db: aiosqlite.Connection, doc_id: str, profile_id: str, label: str, markdown: str
) -> int:
    """Chunk, embed and persist already-converted Markdown. Returns chunk count.

    Shared by file uploads and URL ingestion. Beyond embedding chunks it builds
    the per-document wiki (section tree) and the light knowledge graph
    (document + entity nodes, mentions edges, chunk↔entity links) from the same
    Markdown. On failure the document is marked status='error' and re-raised.
    """
    from app.services import graph_service, wiki_service

    try:
        spans = chunk_markdown_with_offsets(markdown)
        if not spans:
            raise ValueError("No extractable text found in document.")

        await repo.set_markdown(db, doc_id, markdown)
        contents = [c for c, *_rest in spans]
        vectors, embed_model = await embedding_service.embed_texts(contents)
        rows = [
            (idx, content, start, end, section_path, heading, _to_blob(vec))
            for idx, ((content, start, end, section_path, heading), vec) in enumerate(zip(spans, vectors))
        ]
        chunk_ids = await repo.insert_chunks(db, doc_id, profile_id, rows, embed_model)

        # wikillm: derive the wiki tree + knowledge graph from the same Markdown.
        await wiki_service.build_wiki(db, doc_id, profile_id, markdown)
        chunk_meta = [
            {"id": cid, "content": content, "section_path": sp, "heading": hd}
            for cid, (content, _s, _e, sp, hd) in zip(chunk_ids, spans)
        ]
        await graph_service.build_graph(db, doc_id, profile_id, label, markdown, chunk_meta)

        await repo.mark_ready(db, doc_id, len(rows))
        logger.info("Ingested '%s' → %d chunks via %s (+wiki +graph)", label, len(rows), embed_model)
        return len(rows)
    except Exception as exc:
        await repo.mark_error(db, doc_id, str(exc))
        raise


async def reembed(db: aiosqlite.Connection, doc_id: str, profile_id: str) -> int:
    """Rebuild a document from its stored Markdown (re-chunk/embed/wiki/graph).

    Used after changing EMBEDDING_CHAIN or by the batch re-ingest. Returns the
    new chunk count. Legacy documents that predate wikillm fall back to their
    plain source_text.
    """
    from app.services import graph_service

    source = await repo.get_document_source(db, doc_id)
    markdown = (source.markdown if source else None) or (source.source_text if source else None)
    if not source or not markdown:
        raise ValueError("Document has no stored source to re-embed. Re-upload it.")
    await repo.clear_document_derived(db, doc_id)
    await graph_service.clear_document_graph(db, doc_id, profile_id)
    return await ingest_text(db, doc_id, profile_id, source.filename, markdown)


# ── Retrieval ──────────────────────────────────────────────────
def _fts_query(q: str) -> str:
    """Turn a user query into an FTS5 prefix-match OR expression."""
    words = re.sub(r"[^\w\s]", " ", q).split()
    if not words:
        return '""'
    return " OR ".join(f'"{w}"*' for w in words)


def _row_to_source(row: aiosqlite.Row, score: float) -> RagSource:
    keys = row.keys()
    return RagSource(
        document_id=row["document_id"],
        filename=row["filename"],
        chunk_index=row["chunk_index"],
        score=round(float(score), 4),
        snippet=row["content"],
        char_start=row["char_start"] if "char_start" in keys else 0,
        char_end=row["char_end"] if "char_end" in keys else 0,
        section_path=row["section_path"] if "section_path" in keys else None,
    )


async def _vector_candidates(
    db: aiosqlite.Connection,
    profile_id: str,
    query: str,
    pool: int,
    min_score: float,
    document_ids: list[str] | None,
) -> list[tuple[str, float, aiosqlite.Row]]:
    """Return (chunk_id, cosine_score, row) ranked desc, best `pool` kept.

    Uses the sqlite-vec ANN index when available (O(log n)); otherwise falls back
    to the in-memory numpy cosine scan (O(n) per profile).
    """
    query_vecs, embed_model = await embedding_service.embed_texts([query])
    q = np.asarray(query_vecs[0], dtype=np.float32)
    q_norm = np.linalg.norm(q)
    if q_norm == 0:
        logger.warning("RAG retrieve: zero-norm query embedding (model=%s)", embed_model)
        return []

    # ANN path: query the vec0 index directly. Only valid when the query width
    # matches the pinned dimension the index was built with.
    if database.vec_available() and q.shape[0] == int(settings.embedding_dim):
        try:
            rows = await repo.knn_chunks(db, profile_id, q.tobytes(), pool, document_ids)
            scored = [(r["id"], float(r["score"]), r) for r in rows if float(r["score"]) >= min_score]
            if scored or rows:
                return scored[:pool]
        except Exception as exc:
            logger.warning("sqlite-vec KNN failed (%s); falling back to numpy scan", exc)

    # numpy fallback: brute-force cosine over the profile's chunk vectors.
    rows = await repo.iter_chunk_vectors(db, profile_id, document_ids)
    if not rows:
        return []
    scored = []
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
        await _attach_entities(db, results, {cid: row for cid, _s, row in vector[:top_k]})
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

    # wikillm: 1-hop knowledge-graph expansion. Chunks sharing an entity with the
    # current top seeds are pulled in with a damped RRF contribution so they can
    # surface supporting context the lexical/vector arms missed.
    if settings.rag_graph_expand and fused:
        seeds = [cid for cid, _ in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]]
        expanded = await repo.expand_chunks_by_entities(db, profile_id, seeds, pool, document_ids)
        for rank, row in enumerate(expanded):
            cid = row["id"]
            if cid not in rows_by_id:
                rows_by_id[cid] = row
                fused[cid] = fused.get(cid, 0.0) + 0.5 / (_RRF_K + rank + 1)

    ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
    top = ranked[: max(pool, top_k)]
    results = [_row_to_source(rows_by_id[cid], score) for cid, score in top]
    await _attach_entities(db, results, {cid: rows_by_id[cid] for cid, _ in top})

    logger.info(
        "RAG retrieve (hybrid+graph): profile=%s vector=%d lexical=%d fused=%d top_k=%d query=%r",
        profile_id, len(vector), len(lexical), len(ranked), top_k, query[:80],
    )
    return await _maybe_rerank(query, results, top_k)


async def _attach_entities(
    db: aiosqlite.Connection,
    results: list[RagSource],
    rows_by_id: dict[str, aiosqlite.Row],
) -> None:
    """Fill each RagSource.entities from the chunk↔entity graph links."""
    chunk_ids = list(rows_by_id.keys())
    if not chunk_ids:
        return
    try:
        ents = await repo.entities_for_chunks(db, chunk_ids)
    except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
        logger.debug("entity enrichment skipped: %s", exc)
        return
    # results and rows_by_id share chunk ids in the same iteration order.
    for src, cid in zip(results, chunk_ids):
        src.entities = ents.get(cid, [])


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
