"""Chat-trigger session history (Phase 41).

Extracted from the former single-file graph_workflow_repository.py.
"""

import json
import uuid

import aiosqlite

from ._common import _now


# ── chat sessions (Phase 41 — roadmap fase 9.3) ─────────────────────────────

async def get_chat_history(
    db: aiosqlite.Connection, workflow_id: str, session_id: str
) -> list[dict]:
    """The rolling conversation turns for a (workflow, session) — [] when new."""
    async with db.execute(
        "SELECT history_json FROM workflow_chat_sessions WHERE workflow_id = ? AND session_id = ?",
        (workflow_id, session_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return []
    try:
        hist = json.loads(row["history_json"] or "[]")
    except (ValueError, KeyError):
        return []
    return hist if isinstance(hist, list) else []


async def upsert_chat_history(
    db: aiosqlite.Connection, workflow_id: str, profile_id: str,
    session_id: str, history: list[dict],
) -> None:
    now = _now()
    await db.execute(
        "INSERT INTO workflow_chat_sessions (id, session_id, workflow_id, profile_id, history_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(workflow_id, session_id) DO UPDATE SET history_json = excluded.history_json, updated_at = excluded.updated_at",
        (str(uuid.uuid4()), session_id, workflow_id, profile_id, json.dumps(history), now, now),
    )
    await db.commit()


async def purge_stale_chat_sessions(db: aiosqlite.Connection, older_than: int) -> int:
    """Delete chat sessions idle since before ``older_than`` (unix ts). Returns
    the number removed."""
    cur = await db.execute(
        "DELETE FROM workflow_chat_sessions WHERE updated_at < ?", (older_than,)
    )
    await db.commit()
    return cur.rowcount or 0
