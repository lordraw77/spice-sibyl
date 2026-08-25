"""Persistent per-workflow key/value state with TTL (Phase 48).

Extracted from the former single-file graph_workflow_repository.py.
"""

import json

import aiosqlite

from ._common import _now


# ── persistent state (Phase 48 — roadmap fase 16.1) ─────────────────────────
# A per-workflow key/value store that survives across runs. Values are JSON;
# `expires_at` is an optional absolute-epoch TTL. Reads apply lazy expiry (an
# expired key reads as absent); `purge_expired_state` reclaims the rows.

async def state_get(db: aiosqlite.Connection, workflow_id: str, key: str, now: int | None = None) -> tuple[bool, object]:
    """Return ``(found, value)`` for ``key``. A missing or expired key yields
    ``(False, None)``. Never raises on a corrupt row — it reads as absent."""
    now = _now() if now is None else now
    async with db.execute(
        "SELECT value_json, expires_at FROM workflow_state WHERE workflow_id = ? AND key = ?",
        (workflow_id, key),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return False, None
    if row["expires_at"] is not None and row["expires_at"] <= now:
        return False, None
    try:
        return True, json.loads(row["value_json"])
    except (ValueError, TypeError):
        return False, None


async def state_set(
    db: aiosqlite.Connection, workflow_id: str, key: str, value: object, ttl_seconds: int | None = None,
) -> int | None:
    """Upsert ``key`` to ``value``. ``ttl_seconds`` > 0 sets an absolute expiry
    (returned); None/0 clears any expiry (the key persists indefinitely)."""
    now = _now()
    expires = now + int(ttl_seconds) if ttl_seconds and ttl_seconds > 0 else None
    await db.execute(
        "INSERT INTO workflow_state (workflow_id, key, value_json, expires_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(workflow_id, key) DO UPDATE SET "
        "value_json = excluded.value_json, expires_at = excluded.expires_at, updated_at = excluded.updated_at",
        (workflow_id, key, json.dumps(value), expires, now),
    )
    await db.commit()
    return expires


async def state_increment(
    db: aiosqlite.Connection, workflow_id: str, key: str, amount: float = 1, ttl_seconds: int | None = None,
) -> float:
    """Atomically add ``amount`` to the numeric value at ``key`` (0 when missing,
    expired or non-numeric) and return the new value. SQLite's single writer
    makes the read-modify-write atomic against other engine callers."""
    found, current = await state_get(db, workflow_id, key)
    base = current if found and isinstance(current, (int, float)) and not isinstance(current, bool) else 0
    new_value = base + amount
    # Keep integers integral so a counter reads back as 3, not 3.0.
    if isinstance(new_value, float) and new_value.is_integer():
        new_value = int(new_value)
    await state_set(db, workflow_id, key, new_value, ttl_seconds)
    return new_value


async def state_list(db: aiosqlite.Connection, workflow_id: str, now: int | None = None) -> list[dict]:
    """Every live (non-expired) key of the workflow, newest first — for the run
    panel's state inspector (fase 16.1)."""
    now = _now() if now is None else now
    async with db.execute(
        "SELECT key, value_json, expires_at, updated_at FROM workflow_state "
        "WHERE workflow_id = ? AND (expires_at IS NULL OR expires_at > ?) "
        "ORDER BY updated_at DESC",
        (workflow_id, now),
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        try:
            value = json.loads(r["value_json"])
        except (ValueError, TypeError):
            value = None
        out.append({"key": r["key"], "value": value, "expires_at": r["expires_at"], "updated_at": r["updated_at"]})
    return out


async def state_delete(db: aiosqlite.Connection, workflow_id: str, key: str) -> bool:
    cur = await db.execute(
        "DELETE FROM workflow_state WHERE workflow_id = ? AND key = ?", (workflow_id, key)
    )
    await db.commit()
    return (cur.rowcount or 0) > 0


async def purge_expired_state(db: aiosqlite.Connection, now: int | None = None) -> int:
    now = _now() if now is None else now
    cur = await db.execute(
        "DELETE FROM workflow_state WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,)
    )
    await db.commit()
    return cur.rowcount or 0
