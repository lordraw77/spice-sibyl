"""Trigger idempotency keys (Phase 48).

Extracted from the former single-file graph_workflow_repository.py.
"""

import aiosqlite

from ._common import _now


# ── trigger idempotency (Phase 48 — roadmap fase 16.2) ──────────────────────

async def dedup_lookup(db: aiosqlite.Connection, trigger_id: str, key: str, now: int) -> str | None:
    """The ``run_id`` recorded for ``(trigger_id, key)`` if it is still within its
    dedup window, else None (a first — or expired — delivery)."""
    async with db.execute(
        "SELECT run_id FROM workflow_trigger_dedup WHERE trigger_id = ? AND dedup_key = ? AND expires_at > ?",
        (trigger_id, key, now),
    ) as cur:
        row = await cur.fetchone()
    return row["run_id"] if row else None


async def dedup_record(db: aiosqlite.Connection, trigger_id: str, key: str, run_id: str, expires_at: int) -> None:
    """Record (or refresh, when a previous key had expired) the run started for
    ``(trigger_id, key)`` and its new expiry."""
    now = _now()
    await db.execute(
        "INSERT INTO workflow_trigger_dedup (trigger_id, dedup_key, run_id, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(trigger_id, dedup_key) DO UPDATE SET "
        "run_id = excluded.run_id, expires_at = excluded.expires_at, created_at = excluded.created_at",
        (trigger_id, key, run_id, expires_at, now),
    )
    await db.commit()


async def purge_expired_dedup(db: aiosqlite.Connection, now: int | None = None) -> int:
    now = _now() if now is None else now
    cur = await db.execute(
        "DELETE FROM workflow_trigger_dedup WHERE expires_at <= ?", (now,)
    )
    await db.commit()
    return cur.rowcount or 0
