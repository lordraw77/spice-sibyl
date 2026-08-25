"""Profile secrets referenced as $secrets.<name> in expressions.

Extracted from the former single-file graph_workflow_repository.py.
"""

import uuid

import aiosqlite

from ._common import _now


# ── secrets (Phase 32 — roadmap fase 1) ─────────────────────────────────────
# Values arrive/leave this module already encrypted; encryption itself lives in
# vault_service so the key handling stays in one place.

async def upsert_secret(
    db: aiosqlite.Connection, profile_id: str, name: str, value_encrypted: str
) -> None:
    now = _now()
    await db.execute(
        "INSERT INTO workflow_secrets (id, profile_id, name, value_encrypted, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(profile_id, name) DO UPDATE SET value_encrypted = excluded.value_encrypted, updated_at = excluded.updated_at",
        (str(uuid.uuid4()), profile_id, name, value_encrypted, now, now),
    )
    await db.commit()


async def list_secrets(db: aiosqlite.Connection, profile_id: str) -> list[dict]:
    """Names + timestamps only — the encrypted value never leaves the DB layer
    except through :func:`get_encrypted_secrets` for the engine."""
    async with db.execute(
        "SELECT name, created_at, updated_at FROM workflow_secrets WHERE profile_id = ? ORDER BY name",
        (profile_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {"name": r["name"], "created_at": r["created_at"], "updated_at": r["updated_at"]}
        for r in rows
    ]


async def get_encrypted_secrets(db: aiosqlite.Connection, profile_id: str) -> dict[str, str]:
    async with db.execute(
        "SELECT name, value_encrypted FROM workflow_secrets WHERE profile_id = ?", (profile_id,)
    ) as cur:
        rows = await cur.fetchall()
    return {r["name"]: r["value_encrypted"] for r in rows}


async def delete_secret(db: aiosqlite.Connection, profile_id: str, name: str) -> bool:
    cur = await db.execute(
        "DELETE FROM workflow_secrets WHERE profile_id = ? AND name = ?", (profile_id, name)
    )
    await db.commit()
    return cur.rowcount > 0
