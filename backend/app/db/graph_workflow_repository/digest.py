"""Buffered notification digests (Phase 49).

Extracted from the former single-file graph_workflow_repository.py.
"""

import uuid

import aiosqlite

from ._common import _now


# ── Phase 49 (roadmap fase 17.5) — notification digest buffer ───────────────

async def enqueue_digest(
    db: aiosqlite.Connection, workflow_id: str, profile_id: str, channel: str,
    outcome: str, run_id: str,
) -> None:
    await db.execute(
        "INSERT INTO workflow_notification_digest (id, workflow_id, profile_id, channel, outcome, run_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), workflow_id, profile_id, channel, outcome, run_id, _now()),
    )
    await db.commit()


async def list_digest_groups(db: aiosqlite.Connection) -> list[dict]:
    """Distinct (workflow, channel) buckets with a pending digest, with the count
    per outcome and the oldest/newest entry timestamps — the unit the flush sweep
    reasons about ("is this bucket old enough to send?")."""
    async with db.execute(
        "SELECT d.workflow_id, d.profile_id, d.channel, w.name AS workflow_name, w.notify_json, "
        "COUNT(*) AS total, MIN(d.created_at) AS oldest, MAX(d.created_at) AS newest "
        "FROM workflow_notification_digest d JOIN workflows w ON w.id = d.workflow_id "
        "GROUP BY d.workflow_id, d.channel",
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def digest_outcome_counts(
    db: aiosqlite.Connection, workflow_id: str, channel: str
) -> dict[str, int]:
    async with db.execute(
        "SELECT outcome, COUNT(*) AS n FROM workflow_notification_digest "
        "WHERE workflow_id = ? AND channel = ? GROUP BY outcome",
        (workflow_id, channel),
    ) as cur:
        rows = await cur.fetchall()
    return {r["outcome"]: r["n"] for r in rows}


async def clear_digest(db: aiosqlite.Connection, workflow_id: str, channel: str) -> int:
    cur = await db.execute(
        "DELETE FROM workflow_notification_digest WHERE workflow_id = ? AND channel = ?",
        (workflow_id, channel),
    )
    await db.commit()
    return cur.rowcount or 0
