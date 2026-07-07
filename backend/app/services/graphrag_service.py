"""
graphrag_service — Phase 28.d GraphRAG layer over the wikillm knowledge graph.

Three additive capabilities, all built on the existing kb_graph_* tables (no
schema change — communities are stored as `community` nodes + `in_community`
edges, relationships as `related` edges):

  1. LLM entity + relationship extraction (`extract_entities_and_relations`) —
     an opt-in, richer alternative to graph_service's regex heuristic. Returns
     typed entities and entity→entity relationships; graph_service adds the
     'related' edges. Degrades to the LLM-free extractor on any failure.
  2. Community detection + summaries (`build_communities`) — label propagation
     over the fused entity adjacency (co-mention + 'related' edges) groups
     entities into communities; each community gets an LLM (or extractive)
     summary, Microsoft-GraphRAG-style.
  3. Global search (`global_search`) — a map-reduce over community summaries: the
     map step scores + answers per community, the reduce step synthesises a final
     answer. Complements the local (chunk-level) retrieval in rag_service.

Every LLM call is best-effort and cost-bounded; failures degrade gracefully so
ingestion and search never hard-fail because of the GraphRAG layer.
"""

import json
import logging
import re

import aiosqlite

from app.core.config import settings
from app.db import graph_repository as graph_repo

logger = logging.getLogger(__name__)

_MAX_ENTITIES = 60
_MAX_RELATIONS = 80


def _model(*candidates: str) -> str:
    """First non-empty model id in the chain, falling back to default_model."""
    for c in candidates:
        if c:
            return c
    return settings.default_model


async def _llm(prompt: str, model: str, max_tokens: int = 700) -> str:
    """Single-shot completion helper (mirrors rag_service._llm_rerank plumbing)."""
    from app.schemas.chat import ChatCompletionRequest, ChatMessage
    from app.services.provider_factory import ProviderFactory

    req = ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content=prompt)],
        temperature=0.1,
        max_tokens=max_tokens,
    )
    provider = ProviderFactory.get_provider(model)
    resp = await provider.complete(req)
    return resp.choices[0].message.content or ""


def _extract_json(text: str) -> object | None:
    """Pull the first JSON object/array out of an LLM reply (tolerates fences)."""
    match = re.search(r"\{.*\}|\[.*\]", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None


# ── 1. LLM entity + relationship extraction ────────────────────
async def extract_entities_and_relations(
    markdown: str,
) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    """Return (entities, relationships) from Markdown via an LLM.

    entities:      [(label, type), …]
    relationships: [(source_label, target_label, description), …]

    Raises on any failure so the caller can fall back to the regex extractor.
    """
    body = (markdown or "")[: settings.graph_extract_max_chars]
    if not body.strip():
        return [], []
    prompt = (
        "You are a knowledge-graph extractor. From the document below, extract the "
        "most important entities (people, organisations, systems, concepts, places) "
        "and the relationships between them.\n"
        "Return ONLY a JSON object of the form:\n"
        '{"entities": [{"name": "...", "type": "..."}], '
        '"relationships": [{"source": "...", "target": "...", "description": "..."}]}\n'
        f"Extract at most {_MAX_ENTITIES} entities and {_MAX_RELATIONS} relationships. "
        "Use the exact entity names in relationships.\n\n"
        f"Document:\n{body}"
    )
    model = _model(settings.graph_extract_model)
    raw = await _llm(prompt, model, max_tokens=1200)
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise ValueError(f"extractor returned no JSON object: {raw[:120]!r}")

    entities: list[tuple[str, str]] = []
    seen: set[str] = set()
    for e in data.get("entities", []) or []:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name", "")).strip()
        etype = str(e.get("type", "") or "concept").strip().lower()
        key = name.lower()
        if len(name) >= 2 and key not in seen:
            entities.append((name, etype))
            seen.add(key)
        if len(entities) >= _MAX_ENTITIES:
            break

    relationships: list[tuple[str, str, str]] = []
    for r in data.get("relationships", []) or []:
        if not isinstance(r, dict):
            continue
        src = str(r.get("source", "")).strip()
        dst = str(r.get("target", "")).strip()
        desc = str(r.get("description", "")).strip()
        if src and dst and src.lower() != dst.lower():
            relationships.append((src, dst, desc))
        if len(relationships) >= _MAX_RELATIONS:
            break

    logger.info(
        "GraphRAG LLM extraction via %s: %d entities, %d relationships",
        model, len(entities), len(relationships),
    )
    return entities, relationships


# ── 2. Community detection + summaries ─────────────────────────
def detect_communities(
    node_ids: list[str], adjacency: list[tuple[str, str, float]], max_iter: int = 20
) -> dict[str, int]:
    """Label-propagation community detection (deterministic, dependency-free).

    Returns {node_id: community_label}. Isolated nodes keep their own singleton
    community. Determinism (stable node ordering + lowest-id tie-break) keeps
    rebuilds reproducible and the tests meaningful.
    """
    if not node_ids:
        return {}
    order = sorted(set(node_ids))
    labels: dict[str, int] = {nid: i for i, nid in enumerate(order)}

    neighbors: dict[str, dict[str, float]] = {nid: {} for nid in order}
    for a, b, w in adjacency:
        if a in neighbors and b in neighbors and a != b:
            neighbors[a][b] = neighbors[a].get(b, 0.0) + w
            neighbors[b][a] = neighbors[b].get(a, 0.0) + w

    for _ in range(max_iter):
        changed = False
        for nid in order:
            nb = neighbors[nid]
            if not nb:
                continue
            # Weighted vote of neighbour labels; tie-break on the smallest label.
            votes: dict[int, float] = {}
            for other, w in nb.items():
                votes[labels[other]] = votes.get(labels[other], 0.0) + w
            best = min(
                votes.items(), key=lambda kv: (-kv[1], kv[0])
            )[0]
            if labels[nid] != best:
                labels[nid] = best
                changed = True
        if not changed:
            break
    return labels


async def build_communities(
    db: aiosqlite.Connection, profile_id: str
) -> tuple[int, int, int]:
    """(Re)detect communities for a profile and summarise the large ones.

    Returns (communities, summarised, entities). Best-effort: on failure the
    partial state is left in place and the error logged.
    """
    entity_rows = await graph_repo.get_entity_nodes(db, profile_id)
    label_by_id = {r["id"]: r["label"] for r in entity_rows}
    node_ids = list(label_by_id.keys())
    if not node_ids:
        await graph_repo.clear_communities(db, profile_id)
        return 0, 0, 0

    adjacency = await graph_repo.get_entity_adjacency(db, profile_id)
    assignment = detect_communities(node_ids, adjacency)

    groups: dict[int, list[str]] = {}
    for nid, comm in assignment.items():
        groups.setdefault(comm, []).append(nid)

    await graph_repo.clear_communities(db, profile_id)

    min_size = max(2, settings.graph_community_min_size)
    kept = [g for g in groups.values() if len(g) >= min_size]
    kept.sort(key=len, reverse=True)

    created = 0
    summarised = 0
    for members in kept:
        labels = [label_by_id[mid] for mid in members]
        title = _community_title(labels)
        summary, used_llm = await _summarise_community(labels)
        await graph_repo.create_community(db, profile_id, title, summary, 0, members)
        created += 1
        summarised += 1 if used_llm else 0
    await db.commit()
    logger.info(
        "GraphRAG communities for profile %s: %d created (%d LLM-summarised) from %d entities",
        profile_id, created, summarised, len(node_ids),
    )
    return created, summarised, len(node_ids)


def _community_title(labels: list[str]) -> str:
    """Human title from the top member labels (largest-first order preserved)."""
    head = ", ".join(labels[:3])
    return head[:120] if head else "Community"


async def _summarise_community(labels: list[str]) -> tuple[str, bool]:
    """Return (summary, used_llm). Extractive fallback lists the members."""
    extractive = "Related entities: " + ", ".join(labels[:20])
    if not settings.graph_community_summary:
        return extractive, False
    model = _model(settings.graph_community_model, settings.graph_extract_model)
    prompt = (
        "You are summarising a community of related entities from a knowledge base. "
        "In 2–4 sentences, describe what connects these entities and what topics they "
        "cover. Be concise and factual.\n\n"
        f"Entities: {', '.join(labels[:40])}"
    )
    try:
        text = (await _llm(prompt, model, max_tokens=300)).strip()
        return (text or extractive), bool(text)
    except Exception as exc:  # noqa: BLE001 — summary is best-effort
        logger.warning("Community summary failed (%s); using extractive fallback", exc)
        return extractive, False


# ── 3. Global search (map-reduce over community summaries) ─────
async def global_search(
    db: aiosqlite.Connection, profile_id: str, query: str, top_communities: int = 5
) -> dict:
    """Answer a query GraphRAG-style from community summaries.

    Map: each community summary yields a partial answer + a 0–100 relevance score.
    Reduce: the highest-scoring partials are synthesised into a final answer.
    Returns {"query", "answer", "points": [{community_id,title,score,point}]}.
    """
    communities = await graph_repo.list_communities(db, profile_id, limit=40)
    if not communities:
        return {
            "query": query,
            "answer": "No community summaries are available yet. Ingest documents and "
                      "build communities first.",
            "points": [],
        }
    model = _model(settings.graph_community_model, settings.graph_extract_model)

    points: list[dict] = []
    for c in communities:
        summary = c["summary"] or ""
        if not summary.strip():
            continue
        mapped = await _map_community(query, c["label"], summary, model)
        if mapped is None:
            continue
        score, point = mapped
        if score > 0 and point:
            points.append(
                {"community_id": c["id"], "title": c["label"], "score": score, "point": point}
            )

    points.sort(key=lambda p: p["score"], reverse=True)
    top = points[: max(1, top_communities)]
    if not top:
        return {
            "query": query,
            "answer": "The knowledge base communities did not contain information "
                      "relevant to this question.",
            "points": [],
        }

    answer = await _reduce_points(query, top, model)
    return {"query": query, "answer": answer, "points": top}


async def _map_community(
    query: str, title: str, summary: str, model: str
) -> tuple[int, str] | None:
    """Score + answer for one community summary. None on parse failure."""
    prompt = (
        "You are answering a question using ONLY the community summary below. "
        "If the summary is relevant, extract the key point that helps answer the "
        "question and rate its relevance 0–100. If irrelevant, use score 0.\n"
        'Return ONLY JSON: {"score": <0-100>, "point": "<one or two sentences>"}\n\n'
        f"Question: {query}\n\nCommunity: {title}\nSummary: {summary}"
    )
    try:
        data = _extract_json(await _llm(prompt, model, max_tokens=200))
    except Exception as exc:  # noqa: BLE001
        logger.debug("global-search map step failed for %r: %s", title, exc)
        return None
    if not isinstance(data, dict):
        return None
    try:
        score = max(0, min(100, int(data.get("score", 0))))
    except (TypeError, ValueError):
        score = 0
    point = str(data.get("point", "")).strip()
    return score, point


async def _reduce_points(query: str, points: list[dict], model: str) -> str:
    """Synthesise the ranked community points into a final answer."""
    listing = "\n".join(
        f"- ({p['score']}) {p['title']}: {p['point']}" for p in points
    )
    prompt = (
        "Synthesise a single, coherent answer to the question from these ranked "
        "community findings (the number is relevance). Prefer higher-ranked points, "
        "note disagreements, and do not invent facts beyond the findings.\n\n"
        f"Question: {query}\n\nFindings:\n{listing}\n\nAnswer:"
    )
    try:
        return (await _llm(prompt, model, max_tokens=700)).strip() or _fallback_answer(points)
    except Exception as exc:  # noqa: BLE001
        logger.warning("global-search reduce step failed (%s); concatenating points", exc)
        return _fallback_answer(points)


def _fallback_answer(points: list[dict]) -> str:
    return "\n".join(f"• {p['point']}" for p in points if p.get("point"))
