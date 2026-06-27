"""Pydantic schemas for the RAG knowledge base."""

from typing import Literal
from pydantic import BaseModel


class KbDocument(BaseModel):
    """A document stored in the knowledge base."""

    id: str
    profile_id: str
    filename: str
    mime: str | None = None
    size_bytes: int | None = None
    chunk_count: int = 0
    status: Literal["pending", "ready", "error"] = "pending"
    error: str | None = None
    source_type: Literal["file", "url"] = "file"
    source_url: str | None = None
    created_at: int


class RagSource(BaseModel):
    """A retrieved chunk used to ground an assistant reply."""

    document_id: str
    filename: str
    chunk_index: int
    score: float
    snippet: str
    # Phase 17: character span within the source document, enabling the UI to
    # deep-link a citation chip to the exact passage (inline source highlighting).
    char_start: int = 0
    char_end: int = 0


class KbChunk(BaseModel):
    """A single stored chunk (per-document preview)."""

    id: str
    document_id: str
    chunk_index: int
    content: str
    char_start: int = 0
    char_end: int = 0
    embed_model: str | None = None


class KbSearchRequest(BaseModel):
    """Body for POST /v1/knowledge/search (retrieval test)."""

    query: str
    top_k: int = 4
    profile_id: str | None = None
    # Optional scoping: restrict retrieval to these document ids (per-conversation KB scope).
    document_ids: list[str] | None = None


class KbUrlRequest(BaseModel):
    """Body for POST /v1/knowledge/urls (web / URL ingestion)."""

    url: str
    profile_id: str | None = None


class KbDocumentSource(BaseModel):
    """Full source text of a document, for inline highlighting in the reader view."""

    id: str
    filename: str
    source_type: Literal["file", "url"] = "file"
    source_url: str | None = None
    source_text: str | None = None
