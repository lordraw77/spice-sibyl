"""The versioned custom-node registry (Phase 51).

Extracted from the former single-file graph_workflow_repository.py.
"""

import json
import uuid

import aiosqlite

from ._common import _col, _now


# ── custom nodes (Phase 51 / roadmap fase 19) ────────────────────────────────

def _custom_node_row(row) -> dict:
    """Serialise a ``custom_nodes`` row (manifest re-parsed from JSON)."""
    try:
        manifest = json.loads(row["manifest_json"])
    except (ValueError, TypeError):
        manifest = {}
    return {
        "id": row["id"],
        "profile_id": row["profile_id"],
        "type": row["type"],
        "version": row["version"],
        "name": row["name"],
        "description": row["description"],
        "category": row["category"],
        "icon": row["icon"],
        "kind": row["kind"],
        "manifest": manifest,
        "code": _col(row, "code"),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def custom_node_next_version(db: aiosqlite.Connection, profile_id: str, node_type: str) -> int:
    async with db.execute(
        "SELECT MAX(version) AS v FROM custom_nodes WHERE profile_id = ? AND type = ?",
        (profile_id, node_type),
    ) as cur:
        row = await cur.fetchone()
    return (row["v"] or 0) + 1


async def create_custom_node(
    db: aiosqlite.Connection, profile_id: str, node_type: str, *, name: str, description: str,
    category: str, icon: str, kind: str, manifest: dict, code: str | None,
) -> dict:
    """Insert a new version of a custom node. The version is the current max + 1,
    so an existing type is upgraded rather than replaced (old versions keep
    running until a graph migrates)."""
    version = await custom_node_next_version(db, profile_id, node_type)
    now = _now()
    node_id = uuid.uuid4().hex
    await db.execute(
        "INSERT INTO custom_nodes (id, profile_id, type, version, name, description, category, "
        "icon, kind, manifest_json, code, enabled, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (node_id, profile_id, node_type, version, name, description, category, icon, kind,
         json.dumps(manifest), code, now, now),
    )
    await db.commit()
    return await get_custom_node(db, profile_id, node_type)


async def get_custom_node(
    db: aiosqlite.Connection, profile_id: str, node_type: str, version: int | None = None,
) -> dict | None:
    """The current (highest) version of a custom node, or a specific version."""
    if version is None:
        async with db.execute(
            "SELECT * FROM custom_nodes WHERE profile_id = ? AND type = ? "
            "ORDER BY version DESC LIMIT 1",
            (profile_id, node_type),
        ) as cur:
            row = await cur.fetchone()
    else:
        async with db.execute(
            "SELECT * FROM custom_nodes WHERE profile_id = ? AND type = ? AND version = ?",
            (profile_id, node_type, version),
        ) as cur:
            row = await cur.fetchone()
    return _custom_node_row(row) if row else None


async def list_custom_nodes(db: aiosqlite.Connection, profile_id: str, *, enabled_only: bool = False) -> list[dict]:
    """The current version of every custom node the profile owns (newest first)."""
    async with db.execute(
        "SELECT * FROM custom_nodes c WHERE c.profile_id = ? AND c.version = "
        "(SELECT MAX(version) FROM custom_nodes WHERE profile_id = c.profile_id AND type = c.type) "
        "ORDER BY c.updated_at DESC",
        (profile_id,),
    ) as cur:
        rows = await cur.fetchall()
    out = [_custom_node_row(r) for r in rows]
    if enabled_only:
        out = [n for n in out if n["enabled"]]
    return out


async def list_custom_node_versions(db: aiosqlite.Connection, profile_id: str, node_type: str) -> list[dict]:
    async with db.execute(
        "SELECT * FROM custom_nodes WHERE profile_id = ? AND type = ? ORDER BY version DESC",
        (profile_id, node_type),
    ) as cur:
        rows = await cur.fetchall()
    return [_custom_node_row(r) for r in rows]


async def set_custom_node_enabled(
    db: aiosqlite.Connection, profile_id: str, node_type: str, enabled: bool,
) -> bool:
    cur = await db.execute(
        "UPDATE custom_nodes SET enabled = ?, updated_at = ? WHERE profile_id = ? AND type = ?",
        (1 if enabled else 0, _now(), profile_id, node_type),
    )
    await db.commit()
    return (cur.rowcount or 0) > 0


async def delete_custom_node(db: aiosqlite.Connection, profile_id: str, node_type: str) -> bool:
    """Delete every version of a custom node type. Callers must check dependents
    first — this does not."""
    cur = await db.execute(
        "DELETE FROM custom_nodes WHERE profile_id = ? AND type = ?", (profile_id, node_type)
    )
    await db.commit()
    return (cur.rowcount or 0) > 0


async def workflows_using_node_type(db: aiosqlite.Connection, profile_id: str, node_type: str) -> list[dict]:
    """Workflows owned by the profile whose graph references ``node_type`` — the
    dependents that block a delete (roadmap 19.2)."""
    async with db.execute(
        "SELECT id, name, graph_json FROM workflows WHERE profile_id = ?", (profile_id,)
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        try:
            graph = json.loads(r["graph_json"])
        except (ValueError, TypeError):
            continue
        if any((n or {}).get("type") == node_type for n in graph.get("nodes", [])):
            out.append({"id": r["id"], "name": r["name"]})
    return out
