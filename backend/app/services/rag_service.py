"""
rag_service — document ingestion and retrieval for the knowledge base.

Pipeline: extract text → chunk (with overlap) → embed → store vectors as float32
BLOBs in kb_chunks.  Retrieval embeds the query and ranks the profile's chunks by
cosine similarity in numpy (a full per-profile scan, fine for the expected corpus
size; sqlite-vec is the documented upgrade path).
"""

import io
import logging

import aiosqlite
import numpy as np

from app.db import kb_repository as repo
from app.schemas.knowledge import RagSource
from app.services import embedding_service

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 800       # target characters per chunk
_CHUNK_OVERLAP = 120    # characters shared between adjacent chunks


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


# ── Chunking ───────────────────────────────────────────────────
def chunk_text(text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    step = max(size - overlap, 1)
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
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
    """Extract, chunk, embed and persist a document. Returns the chunk count.

    On failure the document is marked status='error' and the exception re-raised.
    """
    try:
        text = extract_text(filename, data)
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError("No extractable text found in document.")

        vectors, embed_model = await embedding_service.embed_texts(chunks)
        rows = [
            (idx, content, _to_blob(vec))
            for idx, (content, vec) in enumerate(zip(chunks, vectors))
        ]
        await repo.insert_chunks(db, doc_id, profile_id, rows, embed_model)
        await repo.mark_ready(db, doc_id, len(rows))
        logger.info("Ingested '%s' → %d chunks via %s", filename, len(rows), embed_model)
        return len(rows)
    except Exception as exc:
        await repo.mark_error(db, doc_id, str(exc))
        raise


# ── Retrieval ──────────────────────────────────────────────────
async def retrieve(
    db: aiosqlite.Connection,
    profile_id: str,
    query: str,
    top_k: int = 4,
    min_score: float = 0.2,
) -> list[RagSource]:
    rows = await repo.iter_chunk_vectors(db, profile_id)
    if not rows:
        logger.info("RAG retrieve: profile=%s has no indexed chunks", profile_id)
        return []

    query_vecs, embed_model = await embedding_service.embed_texts([query])
    q = np.asarray(query_vecs[0], dtype=np.float32)
    q_norm = np.linalg.norm(q)
    if q_norm == 0:
        logger.warning("RAG retrieve: zero-norm query embedding (model=%s)", embed_model)
        return []

    scored: list[tuple[float, aiosqlite.Row]] = []
    skipped_dim = 0
    for row in rows:
        vec = _from_blob(row["embedding"])
        if vec.shape != q.shape:
            # Embedding model changed since ingestion — skip incompatible vectors
            skipped_dim += 1
            continue
        denom = q_norm * np.linalg.norm(vec)
        if denom == 0:
            continue
        score = float(np.dot(q, vec) / denom)
        if score >= min_score:
            scored.append((score, row))

    scored.sort(key=lambda s: s[0], reverse=True)
    top_score = scored[0][0] if scored else 0.0
    logger.info(
        "RAG retrieve: profile=%s scanned=%d matched=%d skipped_dim=%d top_score=%.3f min_score=%.2f query=%r",
        profile_id, len(rows), len(scored), skipped_dim, top_score, min_score, query[:80],
    )
    if skipped_dim:
        logger.warning(
            "RAG retrieve: %d chunk(s) skipped due to embedding dimension mismatch — "
            "re-index documents after changing the embedding model", skipped_dim,
        )
    results: list[RagSource] = []
    for score, row in scored[:top_k]:
        results.append(
            RagSource(
                document_id=row["document_id"],
                filename=row["filename"],
                chunk_index=row["chunk_index"],
                score=round(score, 4),
                # Full chunk text (already bounded to ~_CHUNK_SIZE chars) so the
                # injected context isn't truncated; the UI shows it in a tooltip.
                snippet=row["content"],
            )
        )
    return results


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
