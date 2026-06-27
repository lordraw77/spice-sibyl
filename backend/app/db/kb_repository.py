"""
kb_repository — persistence for the RAG knowledge base.

Documents live in kb_documents; their embedded chunks live in kb_chunks with the
vector stored as a float32 BLOB (numpy.tobytes()).  Follows the plain-async-function
convention of the other repositories (template_repository, tag_repository).
"""

import time
import uuid

import aiosqlite

from app.schemas.knowledge import KbChunk, KbDocument, KbDocumentSource


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
) -> str:
    doc_id = str(uuid.uuid4())
    now = int(time.time())
    await db.execute(
        "INSERT INTO kb_documents (id, profile_id, filename, mime, size_bytes, "
        "chunk_count, status, source_type, source_url, created_at) "
        "VALUES (?, ?, ?, ?, ?, 0, 'pending', ?, ?, ?)",
        (doc_id, profile_id, filename, mime, size_bytes, source_type, source_url, now),
    )
    await db.commit()
    return doc_id


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
    # kb_chunks cascade via the foreign key (PRAGMA foreign_keys=ON in get_db)
    await db.execute("DELETE FROM kb_documents WHERE id = ?", (doc_id,))
    await db.commit()


async def insert_chunks(
    db: aiosqlite.Connection,
    document_id: str,
    profile_id: str,
    chunks: list[tuple[int, str, int, int, bytes]],
    embed_model: str,
) -> None:
    """chunks: list of (chunk_index, content, char_start, char_end, embedding_blob)."""
    now = int(time.time())
    rows = [
        (str(uuid.uuid4()), document_id, profile_id, idx, content, start, end, blob, embed_model, now)
        for (idx, content, start, end, blob) in chunks
    ]
    await db.executemany(
        "INSERT INTO kb_chunks (id, document_id, profile_id, chunk_index, content, "
        "char_start, char_end, embedding, embed_model, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    await db.commit()


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
        "SELECT id, document_id, chunk_index, content, char_start, char_end, embed_model "
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


async def get_document_source(
    db: aiosqlite.Connection, doc_id: str
) -> KbDocumentSource | None:
    async with db.execute(
        "SELECT id, filename, source_type, source_url, source_text "
        "FROM kb_documents WHERE id = ?",
        (doc_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    return KbDocumentSource(
        id=row["id"],
        filename=row["filename"],
        source_type=row["source_type"],
        source_url=row["source_url"],
        source_text=row["source_text"],
    )


async def clear_chunks(db: aiosqlite.Connection, document_id: str) -> None:
    """Remove all chunks of a document (used before re-chunking on re-embed)."""
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
        "c.embedding, d.filename "
        "FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id "
        "WHERE c.profile_id = ? AND d.status = 'ready' AND c.embedding IS NOT NULL" + scope,
        (profile_id, *scope_params),
    ) as cursor:
        return await cursor.fetchall()


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
        "d.filename, bm25(kb_chunks_fts) AS rank "
        "FROM kb_chunks_fts f "
        "JOIN kb_chunks c ON c.id = f.id "
        "JOIN kb_documents d ON d.id = c.document_id "
        "WHERE kb_chunks_fts MATCH ? AND c.profile_id = ? AND d.status = 'ready'" + scope +
        " ORDER BY rank LIMIT ?",
        (fts_query, profile_id, *scope_params, limit),
    ) as cursor:
        return await cursor.fetchall()
