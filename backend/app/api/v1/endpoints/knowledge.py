"""
Knowledge base (RAG) endpoints.

Route map (all under /v1/knowledge):
  GET    /documents              — list KB documents for the current profile
  POST   /documents              — upload + ingest a document (multipart/form-data)
  POST   /urls                   — ingest a web page / URL into the knowledge base
  DELETE /documents/{id}         — delete a document and its chunks
  GET    /documents/{id}/chunks  — preview the stored chunks of a document
  GET    /documents/{id}/source  — full source text (inline highlighting / reader view)
  POST   /documents/{id}/reembed — re-chunk + re-embed from stored source text
  POST   /search                 — retrieval test: rank chunks for a query

Profile identity is conveyed via the X-Profile-ID header (fallback 'default'),
matching the other endpoints; multipart uploads also accept a profile_id field.
"""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.db import kb_repository as repo
from app.db.database import get_db
from app.dependencies.auth import resolve_profile
from app.schemas.knowledge import (
    KbChunk,
    KbDocument,
    KbDocumentSource,
    KbSearchRequest,
    KbUrlRequest,
    RagSource,
)
from app.services import rag_service

router = APIRouter()
logger = logging.getLogger(__name__)

_MAX_BYTES = 20 * 1024 * 1024
_ALLOWED_EXT = (".pdf", ".txt", ".md", ".markdown", ".docx")


@router.get("/documents", response_model=list[KbDocument])
async def list_documents(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    return await repo.list_documents(db, profile_id)


@router.post("/documents", response_model=KbDocument, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    db: aiosqlite.Connection = Depends(get_db),
    pid: str = Depends(resolve_profile),
):
    filename = file.filename or "document"
    if not filename.lower().endswith(_ALLOWED_EXT):
        raise HTTPException(status_code=400, detail="Unsupported file type. Use PDF, TXT, DOCX or Markdown.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 20 MB).")

    logger.info("KB upload: profile=%s file=%r size=%d bytes", pid, filename, len(data))
    doc_id = await repo.create_document(db, pid, filename, file.content_type, len(data))
    try:
        chunk_count = await rag_service.ingest(db, doc_id, pid, filename, data)
        logger.info("KB ingest OK: profile=%s file=%r doc_id=%s chunks=%d", pid, filename, doc_id, chunk_count)
    except Exception as exc:
        logger.warning("KB ingest FAILED: profile=%s file=%r doc_id=%s — %s", pid, filename, doc_id, exc)
        raise HTTPException(status_code=502, detail=f"Ingestion failed: {exc}") from exc

    doc = await repo.get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=500, detail="Document vanished after ingestion.")
    return doc


@router.post("/urls", response_model=KbDocument, status_code=201)
async def ingest_url(
    body: KbUrlRequest,
    db: aiosqlite.Connection = Depends(get_db),
    pid: str = Depends(resolve_profile),
):
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL must not be empty.")

    logger.info("KB URL ingest: profile=%s url=%r", pid, url)
    try:
        text = await rag_service.fetch_url_text(url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch URL: {exc}") from exc
    if not text.strip():
        raise HTTPException(status_code=422, detail="No readable text found at that URL.")

    # Derive a friendly filename from the URL (host + path tail).
    label = url.split("://", 1)[-1].rstrip("/")[:120] or url
    doc_id = await repo.create_document(
        db, pid, label, "text/html", len(text.encode()),
        source_type="url", source_url=url,
    )
    try:
        chunk_count = await rag_service.ingest_text(db, doc_id, pid, label, text)
        logger.info("KB URL ingest OK: profile=%s url=%r doc_id=%s chunks=%d", pid, url, doc_id, chunk_count)
    except Exception as exc:
        logger.warning("KB URL ingest FAILED: profile=%s url=%r — %s", pid, url, exc)
        raise HTTPException(status_code=502, detail=f"Ingestion failed: {exc}") from exc

    doc = await repo.get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=500, detail="Document vanished after ingestion.")
    return doc


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(
    doc_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    await repo.delete_document(db, doc_id)
    return Response(status_code=204)


@router.get("/documents/{doc_id}/chunks", response_model=list[KbChunk])
async def list_document_chunks(
    doc_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    return await repo.get_document_chunks(db, doc_id)


@router.get("/documents/{doc_id}/source", response_model=KbDocumentSource)
async def get_document_source(
    doc_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    source = await repo.get_document_source(db, doc_id)
    if not source:
        raise HTTPException(status_code=404, detail="Document not found.")
    return source


@router.post("/documents/{doc_id}/reembed", response_model=KbDocument)
async def reembed_document(
    doc_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    pid: str = Depends(resolve_profile),
):
    try:
        chunk_count = await rag_service.reembed(db, doc_id, pid)
        logger.info("KB re-embed OK: profile=%s doc_id=%s chunks=%d", pid, doc_id, chunk_count)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("KB re-embed FAILED: profile=%s doc_id=%s — %s", pid, doc_id, exc)
        raise HTTPException(status_code=502, detail=f"Re-embed failed: {exc}") from exc

    doc = await repo.get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found after re-embed.")
    return doc


@router.post("/search", response_model=list[RagSource])
async def search(
    body: KbSearchRequest,
    db: aiosqlite.Connection = Depends(get_db),
    pid: str = Depends(resolve_profile),
):
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty.")
    try:
        return await rag_service.retrieve(
            db, pid, body.query, top_k=body.top_k,
            document_ids=body.document_ids or None,
        )
    except Exception as exc:
        logger.warning("RAG search failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
