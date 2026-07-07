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

import hashlib
import logging

import aiosqlite
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.db import graph_repository as graph_repo
from app.db import kb_repository as repo
from app.db.database import get_db
from app.dependencies.auth import resolve_profile
from app.core.config import settings
from app.schemas.knowledge import (
    CommunityBuildResult,
    GlobalSearchRequest,
    GlobalSearchResponse,
    GraphCommunity,
    GraphNodeDetail,
    GraphRagStatus,
    KbChunk,
    KbDocument,
    KbDocumentSource,
    KbGraph,
    KbSearchRequest,
    KbUrlRequest,
    RagSource,
    WikiPage,
)
from app.services import document_converter, graphrag_service, rag_service, wiki_service

router = APIRouter()
logger = logging.getLogger(__name__)

_MAX_BYTES = 20 * 1024 * 1024
# wikillm: MarkItDown handles a much wider set of formats than the old extractor.
_ALLOWED_EXT = document_converter.SUPPORTED_EXTENSIONS


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
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Accepted: PDF, DOCX, PPTX, XLSX, CSV, HTML, "
            "TXT, Markdown, EPUB, JSON, XML.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 20 MB).")

    # Duplicate detection: hash the raw bytes and reject if the same content is
    # already indexed for this profile (even under a different filename).
    content_hash = hashlib.sha256(data).hexdigest()
    existing = await repo.find_by_hash(db, pid, content_hash)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f'Documento già presente come "{existing.filename}".',
        )

    logger.info("KB upload: profile=%s file=%r size=%d bytes", pid, filename, len(data))
    doc_id = await repo.create_document(
        db, pid, filename, file.content_type, len(data), content_hash=content_hash
    )
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

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    existing = await repo.find_by_hash(db, pid, content_hash)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f'Contenuto già presente come "{existing.filename}".',
        )

    # Derive a friendly filename from the URL (host + path tail).
    label = url.split("://", 1)[-1].rstrip("/")[:120] or url
    doc_id = await repo.create_document(
        db, pid, label, "text/html", len(text.encode()),
        source_type="url", source_url=url, content_hash=content_hash,
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


# ── wikillm: wiki tree, knowledge graph, batch re-ingest ───────────────────────
@router.get("/documents/{doc_id}/wiki", response_model=list[WikiPage])
async def get_document_wiki(
    doc_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Section tree (headings + extractive summaries) built from the Markdown."""
    return await wiki_service.get_wiki(db, doc_id)


@router.get("/graph", response_model=KbGraph)
async def get_graph(
    document_id: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
    pid: str = Depends(resolve_profile),
):
    """The profile's knowledge graph (optionally scoped to one document)."""
    nodes, edges = await graph_repo.get_graph(db, pid, document_id=document_id)
    return KbGraph(nodes=nodes, edges=edges)


@router.get("/graph/nodes/{node_id}", response_model=GraphNodeDetail)
async def get_graph_node(
    node_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    pid: str = Depends(resolve_profile),
):
    """A node with its immediate neighbours and the documents mentioning it."""
    node = await graph_repo.get_node(db, pid, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found.")
    neighbors, edges = await graph_repo.get_neighbors(db, pid, node_id)
    documents: list[KbDocument] = []
    if node.type == "entity":
        doc_ids = await graph_repo.documents_for_entity(db, pid, node_id)
        for did in doc_ids:
            doc = await repo.get_document(db, did)
            if doc:
                documents.append(doc)
    elif node.document_id:
        doc = await repo.get_document(db, node.document_id)
        if doc:
            documents.append(doc)
    return GraphNodeDetail(node=node, neighbors=neighbors, edges=edges, documents=documents)


@router.post("/reingest")
async def reingest(
    db: aiosqlite.Connection = Depends(get_db),
    pid: str = Depends(resolve_profile),
):
    """Rebuild wiki + graph + vectors for pre-wikillm documents (needs_reingest=1).

    Legacy documents keep their stored plain text as the Markdown source, so the
    structure is coarse — re-upload the original file for full fidelity.
    """
    pending = await repo.list_reingest_documents(db, pid)
    rebuilt, failed = 0, 0
    for doc in pending:
        try:
            await rag_service.reembed(db, doc.id, pid)
            rebuilt += 1
        except Exception as exc:
            failed += 1
            logger.warning("Re-ingest failed for doc %s: %s", doc.id, exc)
    # Communities are a global (whole-profile) artefact — rebuild once after the batch.
    if rebuilt:
        try:
            await graphrag_service.build_communities(db, pid)
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning("Community rebuild after re-ingest failed: %s", exc)
    return {"pending": len(pending), "rebuilt": rebuilt, "failed": failed}


# ── Phase 28.d: GraphRAG — communities + global search ─────────────────────────
@router.get("/graph/status", response_model=GraphRagStatus)
async def graphrag_status(
    db: aiosqlite.Connection = Depends(get_db),
    pid: str = Depends(resolve_profile),
):
    """Whether GraphRAG artefacts exist for this profile (drives the UI panel)."""
    return GraphRagStatus(
        llm_extract_enabled=settings.graph_llm_extract,
        global_search_enabled=settings.graphrag_global_search,
        community_count=await graph_repo.count_communities(db, pid),
    )


@router.get("/graph/communities", response_model=list[GraphCommunity])
async def list_communities(
    db: aiosqlite.Connection = Depends(get_db),
    pid: str = Depends(resolve_profile),
):
    """Detected entity communities with their summaries, largest first."""
    rows = await graph_repo.list_communities(db, pid)
    out: list[GraphCommunity] = []
    for r in rows:
        members = await graph_repo.community_member_labels(db, pid, r["id"])
        out.append(
            GraphCommunity(
                id=r["id"],
                title=r["label"],
                summary=r["summary"] or "",
                size=int(r["size"] or 0),
                members=members,
            )
        )
    return out


@router.post("/graph/communities/rebuild", response_model=CommunityBuildResult)
async def rebuild_communities(
    db: aiosqlite.Connection = Depends(get_db),
    pid: str = Depends(resolve_profile),
):
    """Re-detect communities for the profile and (re)generate their summaries."""
    communities, summarised, entities = await graphrag_service.build_communities(db, pid)
    return CommunityBuildResult(
        communities=communities, summarised=summarised, entities=entities
    )


@router.post("/graph/global-search", response_model=GlobalSearchResponse)
async def global_search(
    body: GlobalSearchRequest,
    db: aiosqlite.Connection = Depends(get_db),
    pid: str = Depends(resolve_profile),
):
    """GraphRAG global search: map-reduce an answer over community summaries."""
    if not settings.graphrag_global_search:
        raise HTTPException(status_code=403, detail="Global search is disabled.")
    if not body.query.strip():
        raise HTTPException(status_code=422, detail="Query must not be empty.")
    result = await graphrag_service.global_search(
        db, pid, body.query.strip(), top_communities=body.top_communities
    )
    return GlobalSearchResponse(**result)
