"""
graph_service — build the wikillm knowledge graph from a document's Markdown.

Phase 1 is deliberately LLM-free (near-zero ingest cost): entities come from
Markdown links, headings and a capitalised-phrase frequency heuristic. Each
document becomes a `document` node linked to its `entity` nodes via `mentions`
edges; entities dedupe across documents so shared names connect documents in the
graph view. Chunk↔entity links (kb_chunk_entities) power 1-hop retrieval
expansion. Phase 2 will layer LLM entity/relationship extraction and community
summaries on top of the same tables (see docs/roadmap.md).
"""

import json
import logging
import re
from collections import Counter

import aiosqlite

from app.db import graph_repository as graph_repo
from app.db import kb_repository as kb_repo

logger = logging.getLogger(__name__)

# Cap entities per document so the graph stays legible and ingest stays cheap.
_MAX_ENTITIES = 60
_MIN_FREQ = 2          # capitalised phrases must recur to count (links/headings exempt)
_MIN_LEN = 3

_MD_LINK_RE = re.compile(r"\[([^\]]{2,80})\]\([^)]+\)")
_WIKI_LINK_RE = re.compile(r"\[\[([^\]]{2,80})\]\]")
_HEADING_RE = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*#*\s*$", re.MULTILINE)
_CAP_PHRASE_RE = re.compile(r"\b([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){0,3})\b")

# Common sentence-initial / boilerplate words to drop as standalone entities.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "this", "that", "these",
    "those", "it", "he", "she", "they", "we", "you", "i", "in", "on", "of", "to",
    "for", "with", "as", "at", "by", "from", "into", "figure", "table", "section",
    "chapter", "note", "example", "il", "la", "le", "lo", "gli", "un", "una", "e",
    "di", "da", "che", "per", "con", "questo", "questa",
}


def _clean(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip(" \t*_`#-")).strip()


def extract_entities(markdown: str) -> list[tuple[str, int]]:
    """Extract (label, frequency) candidate entities from Markdown, best-first.

    Markdown/wiki links and headings are always kept; capitalised phrases must
    recur at least `_MIN_FREQ` times. Deduped case-insensitively, capped.
    """
    counts: Counter[str] = Counter()
    forced: set[str] = set()  # keys always kept regardless of frequency

    for rx in (_MD_LINK_RE, _WIKI_LINK_RE, _HEADING_RE):
        for m in rx.finditer(markdown):
            label = _clean(m.group(1))
            if len(label) >= _MIN_LEN and label.lower() not in _STOPWORDS:
                counts[label] += 1
                forced.add(label.lower())

    for m in _CAP_PHRASE_RE.finditer(markdown):
        label = _clean(m.group(1))
        if len(label) < _MIN_LEN or label.lower() in _STOPWORDS:
            continue
        # Drop single-token all-lower-after-first that are just sentence starts by
        # requiring either multi-word or recurrence (handled by _MIN_FREQ below).
        counts[label] += 1

    # Merge case variants by lowercase key: sum frequencies, keep the most common
    # surface form as the display label.
    agg_freq: Counter[str] = Counter()
    best_label: dict[str, tuple[str, int]] = {}  # key → (label, its own freq)
    for label, freq in counts.items():
        key = " ".join(label.lower().split())
        agg_freq[key] += freq
        if key not in best_label or freq > best_label[key][1]:
            best_label[key] = (label, freq)

    entities = [
        (best_label[key][0], total)
        for key, total in agg_freq.items()
        if total >= _MIN_FREQ or key in forced
    ]
    entities.sort(key=lambda t: t[1], reverse=True)
    return entities[:_MAX_ENTITIES]


async def build_graph(
    db: aiosqlite.Connection,
    document_id: str,
    profile_id: str,
    label: str,
    markdown: str,
    chunk_meta: list[dict],
) -> None:
    """Create the document node, entity nodes, mentions edges and chunk links.

    chunk_meta: [{"id", "content", "section_path", "heading"}, …]. Best-effort —
    a failure here must not fail ingestion, so it is logged and swallowed by the
    caller's try/except semantics (build_graph itself does not raise).
    """
    try:
        entities, relationships = await _extract(markdown)
        summary = _document_summary(markdown)
        doc_node = await graph_repo.create_document_node(db, profile_id, document_id, label, summary)

        # Upsert entity nodes + document→entity mentions edges.
        label_to_node: dict[str, str] = {}
        for ent_label, freq in entities:
            node_id = await graph_repo.upsert_entity_node(db, profile_id, ent_label)
            label_to_node[ent_label.lower()] = node_id
            await graph_repo.add_edge(db, profile_id, doc_node, node_id, "mentions", float(freq))

        # Phase 28.d: LLM-extracted entity→entity relationships become 'related'
        # edges (only endpoints that made it into the entity set are linked).
        rel_count = 0
        for src, dst, desc in relationships:
            src_id = label_to_node.get(src.lower())
            dst_id = label_to_node.get(dst.lower())
            if not src_id:
                src_id = await graph_repo.upsert_entity_node(db, profile_id, src)
                label_to_node[src.lower()] = src_id
            if not dst_id:
                dst_id = await graph_repo.upsert_entity_node(db, profile_id, dst)
                label_to_node[dst.lower()] = dst_id
            if src_id != dst_id:
                meta = json.dumps({"description": desc[:300]}) if desc else None
                await graph_repo.add_edge(db, profile_id, src_id, dst_id, "related", 1.0, meta)
                rel_count += 1
        await db.commit()

        # Link chunks to the entities that appear in their text (drives expansion).
        links: list[tuple[str, str]] = []
        lowered = [(el.lower(), nid) for el, nid in label_to_node.items()]
        for cm in chunk_meta:
            content_lc = (cm.get("content") or "").lower()
            for el_lc, nid in lowered:
                if el_lc in content_lc:
                    links.append((cm["id"], nid))
        await kb_repo.link_chunk_entities(db, profile_id, links)
        logger.info(
            "Graph built for doc %s: %d entities, %d relations, %d chunk-links",
            document_id, len(entities), rel_count, len(links),
        )
    except Exception:
        logger.exception("Graph build failed for doc %s (continuing without graph)", document_id)


async def _extract(markdown: str) -> tuple[list[tuple[str, int]], list[tuple[str, str, str]]]:
    """Entities (+relations) for a document.

    Uses the LLM extractor when GRAPH_LLM_EXTRACT is on, degrading to the
    regex/frequency heuristic (and no relationships) on any failure or when off.
    """
    from app.core.config import settings

    if settings.graph_llm_extract:
        try:
            from app.services import graphrag_service

            ents, rels = await graphrag_service.extract_entities_and_relations(markdown)
            if ents:
                # LLM entities carry no frequency; weight mentions by occurrence count.
                lowered = markdown.lower()
                weighted = [(name, max(1, lowered.count(name.lower()))) for name, _t in ents]
                return weighted, rels
            logger.info("LLM extractor returned no entities; using regex heuristic")
        except Exception as exc:  # noqa: BLE001 — always degrade to the free path
            logger.warning("LLM entity extraction failed (%s); using regex heuristic", exc)
    return extract_entities(markdown), []


def _document_summary(markdown: str, max_chars: int = 240) -> str:
    """Extractive one-liner: first non-heading prose of the document."""
    for line in markdown.splitlines():
        s = line.strip(" \t*_`#-")
        if len(s) >= 40 and not line.lstrip().startswith("#"):
            return s[:max_chars]
    return markdown.strip()[:max_chars]


async def clear_document_graph(
    db: aiosqlite.Connection, document_id: str, profile_id: str
) -> None:
    """Remove a document's graph contribution before a rebuild (re-ingest)."""
    await graph_repo.delete_document_graph(db, document_id, profile_id)
