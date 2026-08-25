"""The database-backed message queue driver (Phase 46).

Extracted from the former single-file graph_workflow_repository.py.
"""

import json
import uuid

import aiosqlite

from ._common import _now


# ── message queue (Phase 46 — roadmap fase 14.4) ────────────────────────────
# Backing store for the `db` QueueDriver — see workflow_graph_service.QueueDriver.

async def publish_queue_message(db: aiosqlite.Connection, topic: str, payload: dict, headers: dict) -> str:
    msg_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO workflow_queue_messages (id, topic, payload_json, headers_json, status, created_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?)",
        (msg_id, topic, json.dumps(payload), json.dumps(headers or {}), _now()),
    )
    await db.commit()
    return msg_id


async def consume_queue_messages(db: aiosqlite.Connection, topic: str, limit: int = 10) -> list[dict]:
    """Claim (mark consumed) up to ``limit`` pending messages of ``topic``,
    oldest first. The caller (``_poll_queue_consume``) fires the triggered
    workflow run immediately after claiming each message, so the delivery
    window between the two is as small as a single ``run_workflow`` call —
    a crash inside that narrow window is the only way to lose a message; a
    broker-backed ``QueueDriver`` (a future real adapter) can offer a stronger
    ack-after-completion guarantee by delaying the status flip until then."""
    async with db.execute(
        "SELECT * FROM workflow_queue_messages WHERE topic = ? AND status = 'pending' "
        "ORDER BY created_at ASC LIMIT ?",
        (topic, limit),
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        cur2 = await db.execute(
            "UPDATE workflow_queue_messages SET status = 'consumed', consumed_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (_now(), r["id"]),
        )
        if cur2.rowcount:
            try:
                payload = json.loads(r["payload_json"])
            except (ValueError, TypeError):
                payload = None
            try:
                headers = json.loads(r["headers_json"] or "{}")
            except (ValueError, TypeError):
                headers = {}
            out.append({"id": r["id"], "topic": topic, "message": payload, "headers": headers})
    await db.commit()
    return out
