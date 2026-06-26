"""
Knowledge base (RAG) endpoints.

Route map (all under /v1/knowledge):
  GET    /documents        — list KB documents for the current profile
  POST   /documents        — upload + ingest a document (multipart/form-data)
  DELETE /documents/{id}   — delete a document and its chunks
  POST   /search           — retrieval test: rank chunks for a query

Profile identity is conveyed via the X-Profile-ID header (fallback 'default'),
matching the other endpoints; multipart uploads also accept a profile_id field.
"""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app.db import kb_repository as repo
from app.db.database import get_db
from app.schemas.knowledge import KbDocument, KbSearchRequest, RagSource
from app.services import rag_service

router = APIRouter()
logger = logging.getLogger(__name__)

_DEFAULT_PROFILE = "default"
_MAX_BYTES = 20 * 1024 * 1024
_ALLOWED_EXT = (".pdf", ".txt", ".md", ".markdown", ".docx")


def _profile(x_profile_id: str | None = Header(default=None)) -> str:
    return x_profile_id or _DEFAULT_PROFILE


@router.get("/documents", response_model=list[KbDocument])
async def list_documents(
    profile_id: str = Query(default=_DEFAULT_PROFILE),
    db: aiosqlite.Connection = Depends(get_db),
):
    return await repo.list_documents(db, profile_id)


@router.post("/documents", response_model=KbDocument, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    profile_id_form: str | None = Form(default=None, alias="profile_id"),
    profile_id: str = Depends(_profile),
    db: aiosqlite.Connection = Depends(get_db),
):
    pid = profile_id_form or profile_id
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


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(
    doc_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    await repo.delete_document(db, doc_id)
    return Response(status_code=204)


@router.post("/search", response_model=list[RagSource])
async def search(
    body: KbSearchRequest,
    profile_id: str = Depends(_profile),
    db: aiosqlite.Connection = Depends(get_db),
):
    pid = body.profile_id or profile_id
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty.")
    try:
        return await rag_service.retrieve(db, pid, body.query, top_k=body.top_k)
    except Exception as exc:
        logger.warning("RAG search failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
