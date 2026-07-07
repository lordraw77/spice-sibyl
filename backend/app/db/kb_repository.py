"""
kb_repository — persistence for the RAG knowledge base.

Documents live in kb_documents; their embedded chunks live in kb_chunks with the
vector stored as a float32 BLOB (numpy.tobytes()).  Follows the plain-async-function
convention of the other repositories (template_repository, tag_repository).
"""

import logging
import time
import uuid

import aiosqlite

from app.schemas.knowledge import KbChunk, KbDocument, KbDocumentSource

logger = logging.getLogger(__name__)


def _row_to_document(row: aiosqlite.Row) -> KbDocument:
    keys = row.keys()
    return KbDocument(
        id=row["id"],
        profile_id=row["profile_id"],
        filename=row["filename"],
        mime=row["mime"],
        size_bytes=row["size_bytes"],
        chunk_count=row["chunk_count"],
        status=row["status"],
        error=row["error"],
        source_type=row["source_type"] if "source_type" in keys else "file",
        source_url=row["source_url"] if "source_url" in keys else None,
        needs_reingest=bool(row["needs_reingest"]) if "needs_reingest" in keys else False,
        created_at=row["created_at"],
    )


async def create_document(
    db: aiosqlite.Connection,
    profile_id: str,
    filename: str,
    mime: str | None,
    size_bytes: int | None,
    source_type: str = "file",
    source_url: str | None = None,
    content_hash: str | None = None,
) -> str:
    doc_id = str(uuid.uuid4())
    now = int(time.time())
    await db.execute(
        "INSERT INTO kb_documents (id, profile_id, filename, mime, size_bytes, "
        "chunk_count, status, source_type, source_url, content_hash, created_at) "
        "VALUES (?, ?, ?, ?, ?, 0, 'pending', ?, ?, ?, ?)",
        (doc_id, profile_id, filename, mime, size_bytes, source_type, source_url, content_hash, now),
    )
    await db.commit()
    return doc_id


async def find_by_hash(
    db: aiosqlite.Connection, profile_id: str, content_hash: str
) -> KbDocument | None:
    """Return an existing document with the same content hash in this profile, if any.

    Powers duplicate detection: the same bytes (even re-uploaded under a different
    filename) are recognised. Errored documents don't count as duplicates so a
    failed upload can always be retried.
    """
    async with db.execute(
        "SELECT * FROM kb_documents WHERE profile_id = ? AND content_hash = ? "
        "AND status != 'error' LIMIT 1",
        (profile_id, content_hash),
    ) as cursor:
        row = await cursor.fetchone()
    return _row_to_document(row) if row else None


async def list_documents(
    db: aiosqlite.Connection, profile_id: str
) -> list[KbDocument]:
    async with db.execute(
        "SELECT * FROM kb_documents WHERE profile_id = ? ORDER BY created_at DESC",
        (profile_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_document(r) for r in rows]


async def get_document(
    db: aiosqlite.Connection, doc_id: str
) -> KbDocument | None:
    async with db.execute(
        "SELECT * FROM kb_documents WHERE id = ?", (doc_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return _row_to_document(row) if row else None


async def delete_document(db: aiosqlite.Connection, doc_id: str) -> None:
    # kb_chunks / kb_wiki_pages / kb_chunk_entities cascade via FK (foreign_keys=ON).
    # The ANN mirror and graph (no FK) are cleaned explicitly first.
    from app.db import database, graph_repository

    doc = await get_document(db, doc_id)
    if database.vec_available():
        try:
            await db.execute("DELETE FROM kb_chunk_vec WHERE document_id = ?", (doc_id,))
        except Exception:  # noqa: BLE001
            logger.warning("kb_chunk_vec cleanup failed for doc %s", doc_id)
    if doc:
        await graph_repository.delete_document_graph(db, doc_id, doc.profile_id)
    await db.execute("DELETE FROM kb_documents WHERE id = ?", (doc_id,))
    await db.commit()


async def insert_chunks(
    db: aiosqlite.Connection,
    document_id: str,
    profile_id: str,
    chunks: list[tuple[int, str, int, int, str, str, bytes]],
    embed_model: str,
) -> list[str]:
    """Persist chunks and mirror their vectors into the sqlite-vec ANN table.

    chunks: list of (chunk_index, content, char_start, char_end, section_path,
    heading, embedding_blob). Returns the generated chunk ids in input order so
    the caller can link chunk↔entity graph edges.
    """
    from app.core.config import settings
    from app.db import database

    now = int(time.time())
    ids = [str(uuid.uuid4()) for _ in chunks]
    rows = [
        (cid, document_id, profile_id, idx, content, start, end, section_path, heading, blob, embed_model, now)
        for cid, (idx, content, start, end, section_path, heading, blob) in zip(ids, chunks)
    ]
    await db.executemany(
        "INSERT INTO kb_chunks (id, document_id, profile_id, chunk_index, content, "
        "char_start, char_end, section_path, heading, embedding, embed_model, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )

    # wikillm: mirror vectors into the vec0 ANN table when sqlite-vec is loaded and
    # the vector width matches the pinned dimension. Width mismatches (a different
    # embedding model) silently skip the ANN copy → served by the numpy fallback.
    if database.vec_available():
        dim = int(settings.embedding_dim)
        vec_rows = [
            (cid, profile_id, document_id, blob)
            for cid, (_idx, _c, _s, _e, _sp, _hd, blob) in zip(ids, chunks)
            if len(blob) // 4 == dim
        ]
        if vec_rows:
            try:
                await db.executemany(
                    "INSERT INTO kb_chunk_vec (chunk_id, profile_id, document_id, embedding) "
                    "VALUES (?, ?, ?, ?)",
                    vec_rows,
                )
            except Exception:  # noqa: BLE001 — never fail ingest on the ANN mirror
                logger.warning("kb_chunk_vec insert failed for doc %s; numpy fallback stays", document_id)

    await db.commit()
    return ids


async def replace_chunk_embeddings(
    db: aiosqlite.Connection,
    document_id: str,
    embeddings: list[tuple[str, bytes]],
    embed_model: str,
) -> None:
    """Update embedding vectors in place (re-embed). embeddings: list of (chunk_id, blob)."""
    await db.executemany(
        "UPDATE kb_chunks SET embedding = ?, embed_model = ? WHERE id = ?",
        [(blob, embed_model, cid) for (cid, blob) in embeddings],
    )
    await db.commit()


async def get_document_chunks(
    db: aiosqlite.Connection, document_id: str
) -> list[KbChunk]:
    """All chunks of a document in order (preview / re-embed)."""
    async with db.execute(
        "SELECT id, document_id, chunk_index, content, char_start, char_end, "
        "section_path, heading, embed_model "
        "FROM kb_chunks WHERE document_id = ? ORDER BY chunk_index",
        (document_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        KbChunk(
            id=r["id"],
            document_id=r["document_id"],
            chunk_index=r["chunk_index"],
            content=r["content"],
            char_start=r["char_start"],
            char_end=r["char_end"],
            section_path=r["section_path"],
            heading=r["heading"],
            embed_model=r["embed_model"],
        )
        for r in rows
    ]


async def set_source_text(
    db: aiosqlite.Connection, doc_id: str, source_text: str
) -> None:
    await db.execute(
        "UPDATE kb_documents SET source_text = ? WHERE id = ?", (source_text, doc_id)
    )
    await db.commit()


async def set_markdown(db: aiosqlite.Connection, doc_id: str, markdown: str) -> None:
    """Store the canonical Markdown and clear the re-ingest flag (wikillm)."""
    await db.execute(
        "UPDATE kb_documents SET markdown = ?, source_text = ?, needs_reingest = 0 WHERE id = ?",
        (markdown, markdown, doc_id),
    )
    await db.commit()


async def get_document_source(
    db: aiosqlite.Connection, doc_id: str
) -> KbDocumentSource | None:
    async with db.execute(
        "SELECT id, filename, source_type, source_url, source_text, markdown "
        "FROM kb_documents WHERE id = ?",
        (doc_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    keys = row.keys()
    return KbDocumentSource(
        id=row["id"],
        filename=row["filename"],
        source_type=row["source_type"],
        source_url=row["source_url"],
        source_text=row["source_text"],
        markdown=row["markdown"] if "markdown" in keys else None,
    )


async def list_reingest_documents(
    db: aiosqlite.Connection, profile_id: str
) -> list[KbDocument]:
    """Documents flagged for re-ingest (pre-wikillm) that still have a source."""
    async with db.execute(
        "SELECT * FROM kb_documents WHERE profile_id = ? AND needs_reingest = 1 "
        "AND (markdown IS NOT NULL OR source_text IS NOT NULL) AND status != 'error'",
        (profile_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_document(r) for r in rows]


async def clear_chunks(db: aiosqlite.Connection, document_id: str) -> None:
    """Remove all chunks of a document (used before re-chunking on re-embed)."""
    await clear_document_derived(db, document_id)


async def clear_document_derived(db: aiosqlite.Connection, document_id: str) -> None:
    """Drop everything derived from a document's Markdown before a rebuild.

    Removes chunks (cascading kb_chunk_entities + kb_wiki_pages via FK) and the
    ANN mirror rows. Graph nodes/edges are handled by graph_service.
    """
    from app.db import database

    if database.vec_available():
        try:
            await db.execute("DELETE FROM kb_chunk_vec WHERE document_id = ?", (document_id,))
        except Exception:  # noqa: BLE001
            logger.warning("kb_chunk_vec cleanup failed for doc %s", document_id)
    await db.execute("DELETE FROM kb_chunks WHERE document_id = ?", (document_id,))
    await db.commit()


async def mark_ready(
    db: aiosqlite.Connection, doc_id: str, chunk_count: int
) -> None:
    await db.execute(
        "UPDATE kb_documents SET status = 'ready', chunk_count = ?, error = NULL WHERE id = ?",
        (chunk_count, doc_id),
    )
    await db.commit()


async def mark_error(db: aiosqlite.Connection, doc_id: str, error: str) -> None:
    await db.execute(
        "UPDATE kb_documents SET status = 'error', error = ? WHERE id = ?",
        (error[:500], doc_id),
    )
    await db.commit()


def _doc_scope_clause(document_ids: list[str] | None) -> tuple[str, list]:
    """Build an optional 'AND c.document_id IN (...)' fragment + its params."""
    if not document_ids:
        return "", []
    placeholders = ",".join("?" for _ in document_ids)
    return f" AND c.document_id IN ({placeholders})", list(document_ids)


async def iter_chunk_vectors(
    db: aiosqlite.Connection,
    profile_id: str,
    document_ids: list[str] | None = None,
) -> list[aiosqlite.Row]:
    """Return ready chunks for a profile (optionally scoped to specific documents)."""
    scope, scope_params = _doc_scope_clause(document_ids)
    async with db.execute(
        "SELECT c.id, c.document_id, c.chunk_index, c.content, c.char_start, c.char_end, "
        "c.section_path, c.embedding, d.filename "
        "FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id "
        "WHERE c.profile_id = ? AND d.status = 'ready' AND c.embedding IS NOT NULL" + scope,
        (profile_id, *scope_params),
    ) as cursor:
        return await cursor.fetchall()


async def knn_chunks(
    db: aiosqlite.Connection,
    profile_id: str,
    query_blob: bytes,
    k: int,
    document_ids: list[str] | None = None,
) -> list[aiosqlite.Row]:
    """sqlite-vec ANN search: k nearest chunks (cosine) for the query vector.

    Returns rows with the same shape as iter_chunk_vectors (minus embedding) plus
    a `score` column (1 - cosine distance), best-first. Requires the sqlite-vec
    extension; callers fall back to the numpy scan when it is unavailable.
    """
    # vec0 metadata filtering supports equality on profile_id; document scoping is
    # applied by joining back to kb_chunks (small pool, negligible cost).
    async with db.execute(
        "SELECT v.chunk_id AS id, (1.0 - v.distance) AS score, "
        "c.document_id, c.chunk_index, c.content, c.char_start, c.char_end, "
        "c.section_path, d.filename "
        "FROM kb_chunk_vec v "
        "JOIN kb_chunks c ON c.id = v.chunk_id "
        "JOIN kb_documents d ON d.id = c.document_id "
        "WHERE v.embedding MATCH ? AND v.profile_id = ? AND k = ? AND d.status = 'ready' "
        "ORDER BY v.distance",
        (query_blob, profile_id, k),
    ) as cursor:
        rows = await cursor.fetchall()
    if document_ids:
        allowed = set(document_ids)
        rows = [r for r in rows if r["document_id"] in allowed]
    return rows


async def search_chunks_fts(
    db: aiosqlite.Connection,
    profile_id: str,
    fts_query: str,
    limit: int = 40,
    document_ids: list[str] | None = None,
) -> list[aiosqlite.Row]:
    """Lexical FTS5 search over chunk text, ranked by bm25 (lower = better).

    Returns rows ordered best-first with the same columns as iter_chunk_vectors
    (minus embedding) so the hybrid fuser can treat both result sets uniformly.
    """
    scope, scope_params = _doc_scope_clause(document_ids)
    async with db.execute(
        "SELECT c.id, c.document_id, c.chunk_index, c.content, c.char_start, c.char_end, "
        "c.section_path, d.filename, bm25(kb_chunks_fts) AS rank "
        "FROM kb_chunks_fts f "
        "JOIN kb_chunks c ON c.id = f.id "
        "JOIN kb_documents d ON d.id = c.document_id "
        "WHERE kb_chunks_fts MATCH ? AND c.profile_id = ? AND d.status = 'ready'" + scope +
        " ORDER BY rank LIMIT ?",
        (fts_query, profile_id, *scope_params, limit),
    ) as cursor:
        return await cursor.fetchall()


# ── wikillm: entity links + 1-hop graph expansion ──────────────
async def link_chunk_entities(
    db: aiosqlite.Connection,
    profile_id: str,
    links: list[tuple[str, str]],
) -> None:
    """Persist chunk↔entity-node mentions. links: list of (chunk_id, node_id)."""
    if not links:
        return
    await db.executemany(
        "INSERT OR IGNORE INTO kb_chunk_entities (chunk_id, node_id, profile_id) VALUES (?, ?, ?)",
        [(cid, nid, profile_id) for cid, nid in links],
    )
    await db.commit()


async def entities_for_chunks(
    db: aiosqlite.Connection, chunk_ids: list[str]
) -> dict[str, list[str]]:
    """Map chunk_id → [entity label] for the given chunks (citation enrichment)."""
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" for _ in chunk_ids)
    async with db.execute(
        "SELECT ce.chunk_id, n.label FROM kb_chunk_entities ce "
        "JOIN kb_graph_nodes n ON n.id = ce.node_id "
        f"WHERE ce.chunk_id IN ({placeholders})",
        tuple(chunk_ids),
    ) as cursor:
        rows = await cursor.fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["chunk_id"], []).append(r["label"])
    return out


async def expand_chunks_by_entities(
    db: aiosqlite.Connection,
    profile_id: str,
    seed_chunk_ids: list[str],
    limit: int,
    document_ids: list[str] | None = None,
) -> list[aiosqlite.Row]:
    """1-hop graph expansion: chunks sharing an entity with any seed chunk.

    Excludes the seeds themselves; returns rows shaped like search_chunks_fts
    (no score) so the fuser can treat them uniformly.
    """
    if not seed_chunk_ids:
        return []
    seed_ph = ",".join("?" for _ in seed_chunk_ids)
    scope, scope_params = _doc_scope_clause(document_ids)
    async with db.execute(
        "SELECT DISTINCT c.id, c.document_id, c.chunk_index, c.content, c.char_start, "
        "c.char_end, c.section_path, d.filename "
        "FROM kb_chunk_entities ce "
        "JOIN kb_chunk_entities seed ON seed.node_id = ce.node_id "
        "JOIN kb_chunks c ON c.id = ce.chunk_id "
        "JOIN kb_documents d ON d.id = c.document_id "
        f"WHERE seed.chunk_id IN ({seed_ph}) AND ce.profile_id = ? AND d.status = 'ready' "
        f"AND ce.chunk_id NOT IN ({seed_ph})" + scope +
        " LIMIT ?",
        (*seed_chunk_ids, profile_id, *seed_chunk_ids, *scope_params, limit),
    ) as cursor:
        return await cursor.fetchall()
