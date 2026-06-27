"""Tests for the RAG service: chunking, extraction, ingest→retrieve round-trip."""

import asyncio
import os
import tempfile

import aiosqlite
import pytest

from app.db import kb_repository as repo
from app.db.database import _SCHEMA
from app.services import embedding_service, rag_service

_VOCAB = ["tls", "backup", "nginx", "docker", "certbot", "rag", "cat"]


def _vec(text: str) -> list[float]:
    t = text.lower()
    return [float(t.count(w) + 1) for w in _VOCAB]


async def _fake_embed(texts):
    return [_vec(t) for t in texts], "stub:test"


@pytest.fixture(autouse=True)
def _stub_embeddings(monkeypatch):
    monkeypatch.setattr(embedding_service, "embed_texts", _fake_embed)
    monkeypatch.setattr(rag_service.embedding_service, "embed_texts", _fake_embed)


# ── chunk_text ─────────────────────────────────────────────────
def test_chunk_text_short_returns_single():
    assert rag_service.chunk_text("hello world") == ["hello world"]


def test_chunk_text_empty():
    assert rag_service.chunk_text("   ") == []


def test_chunk_text_overlap_covers_all():
    text = "abcdefghij" * 200  # 2000 chars
    chunks = rag_service.chunk_text(text, size=800, overlap=120)
    assert len(chunks) > 1
    assert all(len(c) <= 800 for c in chunks)
    # Reconstructed coverage: concatenated chunks contain the whole text
    assert text[:50] in chunks[0]
    assert text[-50:] in chunks[-1]


# ── extract_text ───────────────────────────────────────────────
def test_extract_text_markdown_and_txt():
    assert rag_service.extract_text("a.md", b"# Title\nbody") == "# Title\nbody"
    assert rag_service.extract_text("a.txt", b"plain text") == "plain text"


def test_extract_text_unsupported():
    with pytest.raises(ValueError):
        rag_service.extract_text("a.exe", b"x")


# ── ingest → retrieve round-trip ───────────────────────────────
async def _make_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.executescript(_SCHEMA)
    await db.commit()
    await db.execute("PRAGMA foreign_keys=ON")
    return db, path


def test_ingest_and_retrieve_round_trip():
    async def run():
        db, path = await _make_db()
        try:
            text = ("Configurazione TLS con certbot e nginx. " * 40) + (
                "Procedura di backup con docker. " * 40
            )
            doc_id = await repo.create_document(db, "default", "deploy.md", "text/markdown", len(text))
            n = await rag_service.ingest(db, doc_id, "default", "deploy.md", text.encode())
            assert n >= 2

            docs = await repo.list_documents(db, "default")
            assert docs[0].status == "ready"
            assert docs[0].chunk_count == n

            res = await rag_service.retrieve(db, "default", "come faccio il backup?", top_k=3)
            assert res, "expected at least one retrieved chunk"
            # The top chunk for a 'backup' query must actually mention backup
            assert any("backup" in r.snippet.lower() for r in res)

            # Deleting the document cascades to its chunks
            await repo.delete_document(db, doc_id)
            assert await repo.iter_chunk_vectors(db, "default") == []
        finally:
            await db.close()
            os.unlink(path)

    asyncio.run(run())


def test_profile_isolation():
    async def run():
        db, path = await _make_db()
        try:
            text = "secret content about nginx tls " * 30
            doc_id = await repo.create_document(db, "alice", "a.txt", "text/plain", len(text))
            await rag_service.ingest(db, doc_id, "alice", "a.txt", text.encode())
            # Bob must not see Alice's chunks
            res = await rag_service.retrieve(db, "bob", "nginx", top_k=3)
            assert res == []
        finally:
            await db.close()
            os.unlink(path)

    asyncio.run(run())


# ── Phase 17: offsets, hybrid search, scoping, re-embed ────────
def test_chunk_offsets_cover_original_text():
    text = "abcdefghij" * 200  # 2000 chars
    spans = rag_service.chunk_text_with_offsets(text, size=800, overlap=120)
    assert len(spans) > 1
    for content, start, end in spans:
        assert 0 <= start < end <= len(text)
        # The stripped content must be present within the original slice
        assert content in text[start:end]


def test_hybrid_retrieve_lexical_arm_surfaces_match():
    async def run():
        db, path = await _make_db()
        try:
            text = ("Configurazione TLS con certbot e nginx. " * 40) + (
                "Procedura di backup con docker. " * 40
            )
            doc_id = await repo.create_document(db, "default", "deploy.md", "text/markdown", len(text))
            await rag_service.ingest(db, doc_id, "default", "deploy.md", text.encode())

            res = await rag_service.retrieve(db, "default", "backup docker", top_k=3)
            assert res, "hybrid retrieval returned nothing"
            assert any("backup" in r.snippet.lower() for r in res)
            # char offsets are populated for inline highlighting
            assert all(r.char_end >= r.char_start for r in res)
        finally:
            await db.close()
            os.unlink(path)

    asyncio.run(run())


def test_document_scoping_restricts_results():
    async def run():
        db, path = await _make_db()
        try:
            t1 = "nginx tls certbot configuration " * 30
            t2 = "backup docker volume snapshot " * 30
            d1 = await repo.create_document(db, "default", "a.txt", "text/plain", len(t1))
            d2 = await repo.create_document(db, "default", "b.txt", "text/plain", len(t2))
            await rag_service.ingest(db, d1, "default", "a.txt", t1.encode())
            await rag_service.ingest(db, d2, "default", "b.txt", t2.encode())

            scoped = await rag_service.retrieve(
                db, "default", "backup", top_k=5, document_ids=[d1]
            )
            assert all(r.document_id == d1 for r in scoped)
        finally:
            await db.close()
            os.unlink(path)

    asyncio.run(run())


def test_reembed_round_trip():
    async def run():
        db, path = await _make_db()
        try:
            text = "alpha beta nginx tls backup docker " * 40
            doc_id = await repo.create_document(db, "default", "a.txt", "text/plain", len(text))
            n1 = await rag_service.ingest(db, doc_id, "default", "a.txt", text.encode())
            n2 = await rag_service.reembed(db, doc_id, "default")
            assert n1 == n2
            docs = await repo.list_documents(db, "default")
            assert docs[0].status == "ready"
            assert docs[0].chunk_count == n2
            # Re-embed must not duplicate chunks
            chunks = await repo.get_document_chunks(db, doc_id)
            assert len(chunks) == n2
        finally:
            await db.close()
            os.unlink(path)

    asyncio.run(run())
