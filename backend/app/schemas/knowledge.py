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
    created_at: int


class RagSource(BaseModel):
    """A retrieved chunk used to ground an assistant reply."""

    document_id: str
    filename: str
    chunk_index: int
    score: float
    snippet: str


class KbSearchRequest(BaseModel):
    """Body for POST /v1/knowledge/search (retrieval test)."""

    query: str
    top_k: int = 4
    profile_id: str | None = None
