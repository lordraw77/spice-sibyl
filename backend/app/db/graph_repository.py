"""
graph_repository — persistence for the wikillm knowledge graph.

Nodes (`kb_graph_nodes`) are documents or entities; edges (`kb_graph_edges`) are
directed, weighted relations (document→entity `mentions`, entity→entity
`related`, …). Entity nodes dedupe per (profile_id, type, norm_key) so the same
name across documents collapses to one node — that shared node is what links
documents in the graph view. Follows the plain-async-function repo convention.
"""

import logging
import time
import uuid

import aiosqlite

from app.schemas.knowledge import GraphEdge, GraphNode

logger = logging.getLogger(__name__)


def normalize_key(label: str) -> str:
    """Dedup key for entity nodes: case/space-insensitive."""
    return " ".join(label.lower().split())


async def upsert_entity_node(
    db: aiosqlite.Connection, profile_id: str, label: str
) -> str:
    """Return the id of the entity node for `label`, creating it if absent."""
    key = normalize_key(label)
    async with db.execute(
        "SELECT id FROM kb_graph_nodes WHERE profile_id = ? AND type = 'entity' AND norm_key = ?",
        (profile_id, key),
    ) as cursor:
        row = await cursor.fetchone()
    if row:
        return row["id"]
    node_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO kb_graph_nodes (id, profile_id, type, norm_key, label, document_id, "
        "summary, meta_json, created_at) VALUES (?, ?, 'entity', ?, ?, NULL, '', NULL, ?)",
        (node_id, profile_id, key, label, int(time.time())),
    )
    return node_id


async def create_document_node(
    db: aiosqlite.Connection,
    profile_id: str,
    document_id: str,
    label: str,
    summary: str,
) -> str:
    """Create (or replace) the graph node representing a document."""
    key = f"doc:{document_id}"
    node_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO kb_graph_nodes (id, profile_id, type, norm_key, label, document_id, "
        "summary, meta_json, created_at) VALUES (?, ?, 'document', ?, ?, ?, ?, NULL, ?)",
        (node_id, profile_id, key, label, document_id, summary[:500], int(time.time())),
    )
    return node_id


async def add_edge(
    db: aiosqlite.Connection,
    profile_id: str,
    src_node_id: str,
    dst_node_id: str,
    relation: str,
    weight: float = 1.0,
    meta_json: str | None = None,
) -> None:
    await db.execute(
        "INSERT INTO kb_graph_edges (id, profile_id, src_node_id, dst_node_id, relation, "
        "weight, meta_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), profile_id, src_node_id, dst_node_id, relation, weight,
         meta_json, int(time.time())),
    )


def _row_to_node(row: aiosqlite.Row) -> GraphNode:
    keys = row.keys()
    return GraphNode(
        id=row["id"],
        type=row["type"],
        label=row["label"],
        document_id=row["document_id"],
        summary=row["summary"] or "",
        degree=row["degree"] if "degree" in keys else 0,
    )


def _row_to_edge(row: aiosqlite.Row) -> GraphEdge:
    return GraphEdge(
        id=row["id"],
        source=row["src_node_id"],
        target=row["dst_node_id"],
        relation=row["relation"],
        weight=row["weight"],
    )


async def get_graph(
    db: aiosqlite.Connection,
    profile_id: str,
    document_id: str | None = None,
    limit: int = 400,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Return the profile's knowledge (sub)graph for visualisation.

    When `document_id` is given, restrict to that document's node and the
    entities it mentions (plus edges among them). Nodes carry an edge `degree`.
    """
    if document_id:
        async with db.execute(
            "SELECT id FROM kb_graph_nodes WHERE profile_id = ? AND type = 'document' AND document_id = ?",
            (profile_id, document_id),
        ) as cursor:
            drow = await cursor.fetchone()
        if not drow:
            return [], []
        doc_node = drow["id"]
        async with db.execute(
            "SELECT * FROM kb_graph_edges WHERE profile_id = ? AND src_node_id = ? "
            "AND relation != 'in_community'",
            (profile_id, doc_node),
        ) as cursor:
            erows = await cursor.fetchall()
        node_ids = {doc_node} | {e["dst_node_id"] for e in erows} | {e["src_node_id"] for e in erows}
        edges = [_row_to_edge(e) for e in erows]
    else:
        # Community nodes/edges are surfaced through their own endpoint, not the
        # entity-level force-directed view, so exclude them here.
        async with db.execute(
            "SELECT * FROM kb_graph_edges WHERE profile_id = ? AND relation != 'in_community' "
            "ORDER BY weight DESC LIMIT ?",
            (profile_id, limit),
        ) as cursor:
            erows = await cursor.fetchall()
        node_ids = {e["src_node_id"] for e in erows} | {e["dst_node_id"] for e in erows}
        edges = [_row_to_edge(e) for e in erows]

    if not node_ids:
        # No edges yet — still surface isolated document nodes so the view isn't blank.
        async with db.execute(
            "SELECT *, 0 AS degree FROM kb_graph_nodes WHERE profile_id = ? "
            "AND type = 'document' LIMIT ?",
            (profile_id, limit),
        ) as cursor:
            nrows = await cursor.fetchall()
        return [_row_to_node(n) for n in nrows], []

    degree: dict[str, int] = {}
    for e in edges:
        degree[e.source] = degree.get(e.source, 0) + 1
        degree[e.target] = degree.get(e.target, 0) + 1

    placeholders = ",".join("?" for _ in node_ids)
    async with db.execute(
        f"SELECT * FROM kb_graph_nodes WHERE id IN ({placeholders})",
        tuple(node_ids),
    ) as cursor:
        nrows = await cursor.fetchall()
    nodes = []
    for n in nrows:
        node = _row_to_node(n)
        node.degree = degree.get(node.id, 0)
        nodes.append(node)
    return nodes, edges


async def get_node(
    db: aiosqlite.Connection, profile_id: str, node_id: str
) -> GraphNode | None:
    async with db.execute(
        "SELECT *, 0 AS degree FROM kb_graph_nodes WHERE profile_id = ? AND id = ?",
        (profile_id, node_id),
    ) as cursor:
        row = await cursor.fetchone()
    return _row_to_node(row) if row else None


async def get_neighbors(
    db: aiosqlite.Connection, profile_id: str, node_id: str
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Immediate neighbours of a node and the edges connecting them."""
    async with db.execute(
        "SELECT * FROM kb_graph_edges WHERE profile_id = ? AND (src_node_id = ? OR dst_node_id = ?) "
        "AND relation != 'in_community'",
        (profile_id, node_id, node_id),
    ) as cursor:
        erows = await cursor.fetchall()
    edges = [_row_to_edge(e) for e in erows]
    neighbor_ids = {e.source for e in edges} | {e.target for e in edges}
    neighbor_ids.discard(node_id)
    if not neighbor_ids:
        return [], edges
    placeholders = ",".join("?" for _ in neighbor_ids)
    async with db.execute(
        f"SELECT *, 0 AS degree FROM kb_graph_nodes WHERE id IN ({placeholders})",
        tuple(neighbor_ids),
    ) as cursor:
        nrows = await cursor.fetchall()
    return [_row_to_node(n) for n in nrows], edges


async def documents_for_entity(
    db: aiosqlite.Connection, profile_id: str, node_id: str
) -> list[str]:
    """Document ids that mention the given entity node (via kb_chunk_entities)."""
    async with db.execute(
        "SELECT DISTINCT c.document_id FROM kb_chunk_entities ce "
        "JOIN kb_chunks c ON c.id = ce.chunk_id "
        "WHERE ce.node_id = ? AND ce.profile_id = ?",
        (node_id, profile_id),
    ) as cursor:
        rows = await cursor.fetchall()
    return [r["document_id"] for r in rows]


async def delete_document_graph(
    db: aiosqlite.Connection, document_id: str, profile_id: str
) -> None:
    """Remove a document's graph node + its edges, then prune orphan entities.

    Entity nodes are shared across documents, so they are only dropped when no
    edge references them any more (kb_chunk_entities cascades with the chunks).
    """
    async with db.execute(
        "SELECT id FROM kb_graph_nodes WHERE profile_id = ? AND type = 'document' AND document_id = ?",
        (profile_id, document_id),
    ) as cursor:
        drow = await cursor.fetchone()
    if drow:
        doc_node = drow["id"]
        await db.execute(
            "DELETE FROM kb_graph_edges WHERE src_node_id = ? OR dst_node_id = ?",
            (doc_node, doc_node),
        )
        await db.execute("DELETE FROM kb_graph_nodes WHERE id = ?", (doc_node,))
    # Prune entity nodes for this profile that no edge references any more.
    await db.execute(
        "DELETE FROM kb_graph_nodes WHERE profile_id = ? AND type = 'entity' AND id NOT IN "
        "(SELECT src_node_id FROM kb_graph_edges WHERE profile_id = ? "
        " UNION SELECT dst_node_id FROM kb_graph_edges WHERE profile_id = ?)",
        (profile_id, profile_id, profile_id),
    )
    await db.commit()


# ── Phase 28.d: GraphRAG — relationships + communities ─────────────────────────
async def get_entity_nodes(
    db: aiosqlite.Connection, profile_id: str
) -> list[aiosqlite.Row]:
    """All entity nodes for a profile (id, label) — community-detection input."""
    async with db.execute(
        "SELECT id, label FROM kb_graph_nodes WHERE profile_id = ? AND type = 'entity'",
        (profile_id,),
    ) as cursor:
        return await cursor.fetchall()


async def get_entity_adjacency(
    db: aiosqlite.Connection, profile_id: str
) -> list[tuple[str, str, float]]:
    """Weighted undirected entity↔entity adjacency for community detection.

    Fuses two signals so clusters form with or without LLM extraction:
      • explicit entity→entity 'related' edges (their weight), and
      • co-mention: two entities mentioned by the same document (weight 1 each).
    """
    adjacency: list[tuple[str, str, float]] = []

    async with db.execute(
        "SELECT src_node_id, dst_node_id, weight FROM kb_graph_edges "
        "WHERE profile_id = ? AND relation = 'related'",
        (profile_id,),
    ) as cursor:
        for r in await cursor.fetchall():
            adjacency.append((r["src_node_id"], r["dst_node_id"], float(r["weight"] or 1.0)))

    # Co-mention pairs: group entity targets by their document source node.
    async with db.execute(
        "SELECT src_node_id, dst_node_id FROM kb_graph_edges "
        "WHERE profile_id = ? AND relation = 'mentions'",
        (profile_id,),
    ) as cursor:
        mentions = await cursor.fetchall()
    by_doc: dict[str, list[str]] = {}
    for r in mentions:
        by_doc.setdefault(r["src_node_id"], []).append(r["dst_node_id"])
    for ents in by_doc.values():
        for i in range(len(ents)):
            for j in range(i + 1, len(ents)):
                adjacency.append((ents[i], ents[j], 1.0))
    return adjacency


async def clear_communities(db: aiosqlite.Connection, profile_id: str) -> None:
    """Drop a profile's community nodes and their in_community edges (rebuild)."""
    await db.execute(
        "DELETE FROM kb_graph_edges WHERE profile_id = ? AND relation = 'in_community'",
        (profile_id,),
    )
    await db.execute(
        "DELETE FROM kb_graph_nodes WHERE profile_id = ? AND type = 'community'",
        (profile_id,),
    )
    await db.commit()


async def create_community(
    db: aiosqlite.Connection,
    profile_id: str,
    title: str,
    summary: str,
    level: int,
    member_ids: list[str],
) -> str:
    """Persist a community as a graph node + in_community edges to its members."""
    node_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO kb_graph_nodes (id, profile_id, type, norm_key, label, document_id, "
        "summary, meta_json, created_at) VALUES (?, ?, 'community', ?, ?, NULL, ?, ?, ?)",
        (node_id, profile_id, normalize_key(title), title, summary[:4000],
         f'{{"level": {int(level)}, "size": {len(member_ids)}}}', int(time.time())),
    )
    for mid in member_ids:
        await add_edge(db, profile_id, node_id, mid, "in_community", 1.0)
    return node_id


async def list_communities(
    db: aiosqlite.Connection, profile_id: str, limit: int = 100
) -> list[aiosqlite.Row]:
    """Community nodes for a profile, largest first (by member count)."""
    async with db.execute(
        "SELECT n.id, n.label, n.summary, n.meta_json, "
        "  (SELECT COUNT(*) FROM kb_graph_edges e WHERE e.src_node_id = n.id "
        "   AND e.relation = 'in_community') AS size "
        "FROM kb_graph_nodes n WHERE n.profile_id = ? AND n.type = 'community' "
        "ORDER BY size DESC LIMIT ?",
        (profile_id, limit),
    ) as cursor:
        return await cursor.fetchall()


async def community_member_labels(
    db: aiosqlite.Connection, profile_id: str, community_id: str, limit: int = 12
) -> list[str]:
    """Labels of the entity nodes belonging to a community (for display)."""
    async with db.execute(
        "SELECT n.label FROM kb_graph_edges e JOIN kb_graph_nodes n ON n.id = e.dst_node_id "
        "WHERE e.profile_id = ? AND e.src_node_id = ? AND e.relation = 'in_community' "
        "LIMIT ?",
        (profile_id, community_id, limit),
    ) as cursor:
        return [r["label"] for r in await cursor.fetchall()]


async def count_communities(db: aiosqlite.Connection, profile_id: str) -> int:
    async with db.execute(
        "SELECT COUNT(*) AS c FROM kb_graph_nodes WHERE profile_id = ? AND type = 'community'",
        (profile_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def update_node_summary(
    db: aiosqlite.Connection, node_id: str, summary: str
) -> None:
    """Replace a node's summary (used by LLM wiki / entity summarisation)."""
    await db.execute(
        "UPDATE kb_graph_nodes SET summary = ? WHERE id = ?", (summary[:4000], node_id)
    )
