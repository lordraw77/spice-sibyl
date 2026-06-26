"""
kb_repository — persistence for the RAG knowledge base.

Documents live in kb_documents; their embedded chunks live in kb_chunks with the
vector stored as a float32 BLOB (numpy.tobytes()).  Follows the plain-async-function
convention of the other repositories (template_repository, tag_repository).
"""

import time
import uuid

import aiosqlite

from app.schemas.knowledge import KbDocument


def _row_to_document(row: aiosqlite.Row) -> KbDocument:
    return KbDocument(
        id=row["id"],
        profile_id=row["profile_id"],
        filename=row["filename"],
        mime=row["mime"],
        size_bytes=row["size_bytes"],
        chunk_count=row["chunk_count"],
        status=row["status"],
        error=row["error"],
        created_at=row["created_at"],
    )


async def create_document(
    db: aiosqlite.Connection,
    profile_id: str,
    filename: str,
    mime: str | None,
    size_bytes: int | None,
) -> str:
    doc_id = str(uuid.uuid4())
    now = int(time.time())
    await db.execute(
        "INSERT INTO kb_documents (id, profile_id, filename, mime, size_bytes, "
        "chunk_count, status, created_at) VALUES (?, ?, ?, ?, ?, 0, 'pending', ?)",
        (doc_id, profile_id, filename, mime, size_bytes, now),
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
    chunks: list[tuple[int, str, bytes]],
    embed_model: str,
) -> None:
    """chunks: list of (chunk_index, content, embedding_blob)."""
    now = int(time.time())
    rows = [
        (str(uuid.uuid4()), document_id, profile_id, idx, content, blob, embed_model, now)
        for (idx, content, blob) in chunks
    ]
    await db.executemany(
        "INSERT INTO kb_chunks (id, document_id, profile_id, chunk_index, content, "
        "embedding, embed_model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
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


async def iter_chunk_vectors(
    db: aiosqlite.Connection, profile_id: str
) -> list[aiosqlite.Row]:
    """Return all ready chunks for a profile: id, document_id, chunk_index, content, embedding."""
    async with db.execute(
        "SELECT c.document_id, c.chunk_index, c.content, c.embedding, d.filename "
        "FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id "
        "WHERE c.profile_id = ? AND d.status = 'ready' AND c.embedding IS NOT NULL",
        (profile_id,),
    ) as cursor:
        return await cursor.fetchall()
