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
    # wikillm: set when a pre-wikillm document must be re-ingested to build its
    # Markdown / wiki / graph (drives the "re-ingest needed" banner).
    needs_reingest: bool = False
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
    # wikillm: section breadcrumb + entities mentioned in the chunk (graph context).
    section_path: str | None = None
    entities: list[str] = []


class KbChunk(BaseModel):
    """A single stored chunk (per-document preview)."""

    id: str
    document_id: str
    chunk_index: int
    content: str
    char_start: int = 0
    char_end: int = 0
    embed_model: str | None = None
    # wikillm: section breadcrumb + heading this chunk belongs to.
    section_path: str | None = None
    heading: str | None = None


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
    """Full source of a document, for inline highlighting in the reader view."""

    id: str
    filename: str
    source_type: Literal["file", "url"] = "file"
    source_url: str | None = None
    source_text: str | None = None
    # wikillm: canonical Markdown (MarkItDown output); citations index into this.
    markdown: str | None = None


# ── wikillm: wiki tree + knowledge graph ───────────────────────
class WikiPage(BaseModel):
    """One node of a document's section tree (built from Markdown headings)."""

    id: str
    document_id: str
    parent_id: str | None = None
    heading: str
    level: int = 1
    char_start: int = 0
    char_end: int = 0
    summary: str = ""
    ord: int = 0


class GraphNode(BaseModel):
    """A knowledge-graph node (document / section / entity / community)."""

    id: str
    type: Literal["document", "section", "entity", "community"]
    label: str
    document_id: str | None = None
    summary: str = ""
    # Number of graph edges touching this node (sized/ranked in the UI).
    degree: int = 0


class GraphEdge(BaseModel):
    """A directed, weighted knowledge-graph edge."""

    id: str
    source: str
    target: str
    relation: Literal["contains", "mentions", "links_to", "related", "in_community"]
    weight: float = 1.0


class KbGraph(BaseModel):
    """A knowledge (sub)graph for visualisation."""

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []


class GraphNodeDetail(BaseModel):
    """A node plus its immediate neighbours (node-detail view)."""

    node: GraphNode
    neighbors: list[GraphNode] = []
    edges: list[GraphEdge] = []
    documents: list[KbDocument] = []


# ── Phase 28.d: GraphRAG (communities + global search) ─────────
class GraphCommunity(BaseModel):
    """A detected community of related entities with an LLM (or extractive) summary."""

    id: str
    title: str
    summary: str = ""
    level: int = 0
    size: int = 0            # number of member entity nodes
    members: list[str] = []  # member entity labels (top ones, for display)


class GraphRagStatus(BaseModel):
    """Whether GraphRAG artefacts exist for a profile (drives the UI panel)."""

    llm_extract_enabled: bool = False
    global_search_enabled: bool = False
    community_count: int = 0


class CommunityBuildResult(BaseModel):
    """Outcome of a community (re)build for a profile."""

    communities: int = 0
    summarised: int = 0
    entities: int = 0


class GlobalSearchRequest(BaseModel):
    """Body for POST /v1/knowledge/graph/global-search."""

    query: str
    profile_id: str | None = None
    # How many top community summaries feed the reduce step.
    top_communities: int = 5


class GlobalSearchPoint(BaseModel):
    """One community's map-step contribution to a global answer."""

    community_id: str
    title: str
    score: int = 0            # 0–100 relevance the map step assigned
    point: str = ""           # the community's partial answer


class GlobalSearchResponse(BaseModel):
    """GraphRAG-style global answer synthesised from community summaries."""

    query: str
    answer: str
    points: list[GlobalSearchPoint] = []
