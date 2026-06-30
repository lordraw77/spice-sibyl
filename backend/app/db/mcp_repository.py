"""
Phase 18 — MCP server registry repository.

CRUD over the ``mcp_servers`` table. The per-server config (command/args/env/...)
is stored verbatim as a JSON string in the ``config`` column, so the standard
``mcpServers`` shape round-trips losslessly.
"""

import json
import time
import uuid

import aiosqlite

from app.schemas.mcp import McpServerConfig, McpServerOut


def _row_to_out(row: aiosqlite.Row) -> McpServerOut:
    config = McpServerConfig.model_validate(json.loads(row["config"]))
    return McpServerOut(
        id=row["id"],
        name=row["name"],
        config=config,
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        status="disabled" if not row["enabled"] else "unknown",
    )


async def list_servers(db: aiosqlite.Connection) -> list[McpServerOut]:
    async with db.execute("SELECT * FROM mcp_servers ORDER BY name") as cursor:
        rows = await cursor.fetchall()
    return [_row_to_out(r) for r in rows]


async def get_server(db: aiosqlite.Connection, server_id: str) -> McpServerOut | None:
    async with db.execute("SELECT * FROM mcp_servers WHERE id = ?", (server_id,)) as cursor:
        row = await cursor.fetchone()
    return _row_to_out(row) if row else None


async def get_by_name(db: aiosqlite.Connection, name: str) -> McpServerOut | None:
    async with db.execute("SELECT * FROM mcp_servers WHERE name = ?", (name,)) as cursor:
        row = await cursor.fetchone()
    return _row_to_out(row) if row else None


async def upsert_server(
    db: aiosqlite.Connection, name: str, config: McpServerConfig, enabled: bool
) -> McpServerOut:
    """Create a server, or replace the config/enabled of an existing one (by name)."""
    now = int(time.time())
    config_json = json.dumps(config.model_dump(exclude_none=True))
    existing = await get_by_name(db, name)
    if existing:
        await db.execute(
            "UPDATE mcp_servers SET config = ?, enabled = ?, updated_at = ? WHERE id = ?",
            (config_json, int(enabled), now, existing.id),
        )
        await db.commit()
        return await get_server(db, existing.id)  # type: ignore[return-value]
    server_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO mcp_servers (id, name, config, enabled, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (server_id, name, config_json, int(enabled), now, now),
    )
    await db.commit()
    return await get_server(db, server_id)  # type: ignore[return-value]


async def set_enabled(
    db: aiosqlite.Connection, server_id: str, enabled: bool
) -> McpServerOut | None:
    server = await get_server(db, server_id)
    if not server:
        return None
    await db.execute(
        "UPDATE mcp_servers SET enabled = ?, updated_at = ? WHERE id = ?",
        (int(enabled), int(time.time()), server_id),
    )
    await db.commit()
    return await get_server(db, server_id)


async def delete_server(db: aiosqlite.Connection, server_id: str) -> bool:
    cursor = await db.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
    await db.commit()
    return cursor.rowcount > 0
