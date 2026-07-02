"""
Phase 18 — user-defined custom tools repository.

CRUD over the ``custom_tools`` table. ``parameters`` (JSON schema) and
``endpoint`` (url/method/headers/auth/timeout) are stored as JSON strings.
Tools are scoped per profile; the name is unique within a profile.
"""

import json
import time
import uuid

import aiosqlite

from app.schemas.custom_tools import CustomToolEndpoint, CustomToolIn, CustomToolOut


def _row_to_out(row: aiosqlite.Row) -> CustomToolOut:
    return CustomToolOut(
        id=row["id"],
        profile_id=row["profile_id"],
        name=row["name"],
        description=row["description"] or "",
        parameters=json.loads(row["parameters"]),
        endpoint=CustomToolEndpoint.model_validate(json.loads(row["endpoint"])),
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def list_tools(
    db: aiosqlite.Connection, profile_id: str, enabled_only: bool = False
) -> list[CustomToolOut]:
    sql = "SELECT * FROM custom_tools WHERE profile_id = ?"
    if enabled_only:
        sql += " AND enabled = 1"
    sql += " ORDER BY name"
    async with db.execute(sql, (profile_id,)) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_out(r) for r in rows]


async def get_tool(db: aiosqlite.Connection, tool_id: str) -> CustomToolOut | None:
    async with db.execute("SELECT * FROM custom_tools WHERE id = ?", (tool_id,)) as cursor:
        row = await cursor.fetchone()
    return _row_to_out(row) if row else None


async def get_by_name(
    db: aiosqlite.Connection, profile_id: str, name: str
) -> CustomToolOut | None:
    async with db.execute(
        "SELECT * FROM custom_tools WHERE profile_id = ? AND name = ?", (profile_id, name)
    ) as cursor:
        row = await cursor.fetchone()
    return _row_to_out(row) if row else None


async def upsert_tool(
    db: aiosqlite.Connection, profile_id: str, body: CustomToolIn
) -> CustomToolOut:
    """Create a tool, or replace an existing one with the same (profile, name)."""
    now = int(time.time())
    params_json = json.dumps(body.parameters)
    endpoint_json = json.dumps(body.endpoint.model_dump(exclude_none=True))
    existing = await get_by_name(db, profile_id, body.name)
    if existing:
        await db.execute(
            "UPDATE custom_tools SET description = ?, parameters = ?, endpoint = ?, "
            "enabled = ?, updated_at = ? WHERE id = ?",
            (body.description, params_json, endpoint_json, int(body.enabled), now, existing.id),
        )
        await db.commit()
        return await get_tool(db, existing.id)  # type: ignore[return-value]
    tool_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO custom_tools "
        "(id, profile_id, name, description, parameters, endpoint, enabled, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (tool_id, profile_id, body.name, body.description, params_json,
         endpoint_json, int(body.enabled), now, now),
    )
    await db.commit()
    return await get_tool(db, tool_id)  # type: ignore[return-value]


async def set_enabled(
    db: aiosqlite.Connection, tool_id: str, enabled: bool
) -> CustomToolOut | None:
    tool = await get_tool(db, tool_id)
    if not tool:
        return None
    await db.execute(
        "UPDATE custom_tools SET enabled = ?, updated_at = ? WHERE id = ?",
        (int(enabled), int(time.time()), tool_id),
    )
    await db.commit()
    return await get_tool(db, tool_id)


async def delete_tool(db: aiosqlite.Connection, tool_id: str) -> bool:
    cursor = await db.execute("DELETE FROM custom_tools WHERE id = ?", (tool_id,))
    await db.commit()
    return cursor.rowcount > 0
