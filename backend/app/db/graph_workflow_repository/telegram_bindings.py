"""Telegram /command to workflow bindings (Phase 52).

Extracted from the former single-file graph_workflow_repository.py.
"""

import uuid

import aiosqlite

from ._common import _now


# ── telegram command bindings (Phase 52 / roadmap fase 20.5) ──────────────────

def _tg_binding_row(row) -> dict:
    return {
        "id": row["id"],
        "profile_id": row["profile_id"],
        "command": row["command"],
        "workflow_id": row["workflow_id"],
        "description": row["description"],
        "created_at": row["created_at"],
    }


async def create_telegram_binding(
    db: aiosqlite.Connection, profile_id: str, command: str, workflow_id: str, description: str = "",
) -> dict:
    """Bind a bot command to a workflow. Raises ``ValueError`` on a collision
    (the command is already claimed in this profile)."""
    command = command.lstrip("/").strip().lower()
    existing = await get_telegram_binding(db, profile_id, command)
    if existing is not None:
        raise ValueError(f"command '/{command}' is already bound to another workflow")
    now = _now()
    binding_id = uuid.uuid4().hex
    await db.execute(
        "INSERT INTO telegram_command_bindings (id, profile_id, command, workflow_id, description, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (binding_id, profile_id, command, workflow_id, description, now),
    )
    await db.commit()
    return await get_telegram_binding(db, profile_id, command)


async def get_telegram_binding(db: aiosqlite.Connection, profile_id: str, command: str) -> dict | None:
    command = command.lstrip("/").strip().lower()
    async with db.execute(
        "SELECT * FROM telegram_command_bindings WHERE profile_id = ? AND command = ?",
        (profile_id, command),
    ) as cur:
        row = await cur.fetchone()
    return _tg_binding_row(row) if row else None


async def find_telegram_binding_by_command(db: aiosqlite.Connection, command: str) -> dict | None:
    """Look a command up across all profiles (the bot is a single deployment).
    First match wins — collisions within a profile are already prevented."""
    command = command.lstrip("/").strip().lower()
    async with db.execute(
        "SELECT * FROM telegram_command_bindings WHERE command = ? ORDER BY created_at LIMIT 1",
        (command,),
    ) as cur:
        row = await cur.fetchone()
    return _tg_binding_row(row) if row else None


async def list_telegram_bindings(db: aiosqlite.Connection, profile_id: str) -> list[dict]:
    async with db.execute(
        "SELECT * FROM telegram_command_bindings WHERE profile_id = ? ORDER BY command",
        (profile_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_tg_binding_row(r) for r in rows]


async def list_all_telegram_bindings(db: aiosqlite.Connection) -> list[dict]:
    async with db.execute(
        "SELECT * FROM telegram_command_bindings ORDER BY command"
    ) as cur:
        rows = await cur.fetchall()
    return [_tg_binding_row(r) for r in rows]


async def delete_telegram_binding(db: aiosqlite.Connection, profile_id: str, command: str) -> bool:
    command = command.lstrip("/").strip().lower()
    cur = await db.execute(
        "DELETE FROM telegram_command_bindings WHERE profile_id = ? AND command = ?",
        (profile_id, command),
    )
    await db.commit()
    return (cur.rowcount or 0) > 0
