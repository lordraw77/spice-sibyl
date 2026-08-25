"""Remote runners and the jobs handed to them (Phase 46).

Extracted from the former single-file graph_workflow_repository.py.
"""

import hashlib
import json
import secrets
import uuid

import aiosqlite

from app.schemas.graph_workflows import RunnerOut

from ._common import _col, _now


# ── remote runners (Phase 46 — roadmap fase 14.1) ───────────────────────────

def _hash_runner_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _row_to_runner(row: aiosqlite.Row, *, heartbeat_timeout: int) -> RunnerOut:
    last_hb = _col(row, "last_heartbeat_at")
    online = bool(last_hb) and (_now() - last_hb) <= heartbeat_timeout
    try:
        labels = json.loads(row["labels_json"] or "[]")
    except (ValueError, TypeError):
        labels = []
    try:
        allowed = json.loads(row["allowed_node_types_json"] or "[]")
    except (ValueError, TypeError):
        allowed = []
    return RunnerOut(
        id=row["id"],
        name=row["name"],
        labels=labels if isinstance(labels, list) else [],
        allowed_node_types=allowed if isinstance(allowed, list) else [],
        version=_col(row, "version"),
        status="online" if online else "offline",
        last_heartbeat_at=last_hb,
        created_at=row["created_at"],
    )


async def create_runner(
    db: aiosqlite.Connection, profile_id: str, name: str,
    labels: list[str], allowed_node_types: list[str],
) -> tuple[str, str]:
    """Provision a runner slot; returns (id, raw_token) — the raw token is
    NEVER stored (only its sha256) and never retrievable again."""
    runner_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    await db.execute(
        "INSERT INTO workflow_runners (id, profile_id, name, token_hash, labels_json, allowed_node_types_json, revoked, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
        (runner_id, profile_id, name, _hash_runner_token(token),
         json.dumps(labels), json.dumps(allowed_node_types), _now()),
    )
    await db.commit()
    return runner_id, token


async def get_runner_row(db: aiosqlite.Connection, runner_id: str) -> aiosqlite.Row | None:
    async with db.execute(
        "SELECT * FROM workflow_runners WHERE id = ? AND revoked = 0", (runner_id,)
    ) as cur:
        return await cur.fetchone()


async def get_runner_by_token(db: aiosqlite.Connection, token: str) -> aiosqlite.Row | None:
    async with db.execute(
        "SELECT * FROM workflow_runners WHERE token_hash = ? AND revoked = 0",
        (_hash_runner_token(token),),
    ) as cur:
        return await cur.fetchone()


async def list_runners(db: aiosqlite.Connection, profile_id: str, *, heartbeat_timeout: int) -> list[RunnerOut]:
    async with db.execute(
        "SELECT * FROM workflow_runners WHERE profile_id = ? AND revoked = 0 ORDER BY created_at DESC",
        (profile_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_runner(r, heartbeat_timeout=heartbeat_timeout) for r in rows]


async def find_online_runners(
    db: aiosqlite.Connection, profile_id: str, label: str, node_type: str, *, heartbeat_timeout: int,
) -> list[dict]:
    """Runners of the profile that: are online (heartbeat within the timeout),
    carry ``label``, and (empty allow-list, or) allow ``node_type``. Ordered
    oldest-heartbeat-first so load spreads round-robin-ish across runners."""
    cutoff = _now() - heartbeat_timeout
    async with db.execute(
        "SELECT * FROM workflow_runners WHERE profile_id = ? AND revoked = 0 "
        "AND last_heartbeat_at IS NOT NULL AND last_heartbeat_at >= ? "
        "ORDER BY last_heartbeat_at ASC",
        (profile_id, cutoff),
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        try:
            labels = json.loads(r["labels_json"] or "[]")
        except (ValueError, TypeError):
            labels = []
        if label not in labels:
            continue
        try:
            allowed = json.loads(r["allowed_node_types_json"] or "[]")
        except (ValueError, TypeError):
            allowed = []
        if allowed and node_type not in allowed:
            continue
        out.append(dict(r))
    return out


async def heartbeat_runner(
    db: aiosqlite.Connection, runner_id: str, *, version: str | None, labels: list[str] | None,
) -> None:
    if labels is not None:
        await db.execute(
            "UPDATE workflow_runners SET last_heartbeat_at = ?, version = COALESCE(?, version), labels_json = ? WHERE id = ?",
            (_now(), version, json.dumps(labels), runner_id),
        )
    else:
        await db.execute(
            "UPDATE workflow_runners SET last_heartbeat_at = ?, version = COALESCE(?, version) WHERE id = ?",
            (_now(), version, runner_id),
        )
    await db.commit()


async def revoke_runner(db: aiosqlite.Connection, runner_id: str) -> None:
    await db.execute("UPDATE workflow_runners SET revoked = 1 WHERE id = ?", (runner_id,))
    await db.commit()


# ── remote runner jobs (Phase 46 — roadmap fase 14.1) ───────────────────────

async def create_runner_job(
    db: aiosqlite.Connection, runner_id: str, run_id: str | None,
    node_id: str, node_type: str, payload: dict,
) -> str:
    job_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO workflow_runner_jobs (id, runner_id, run_id, node_id, node_type, payload_json, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)",
        (job_id, runner_id, run_id, node_id, node_type, json.dumps(payload), _now()),
    )
    await db.commit()
    return job_id


async def get_runner_job(db: aiosqlite.Connection, job_id: str) -> aiosqlite.Row | None:
    async with db.execute("SELECT * FROM workflow_runner_jobs WHERE id = ?", (job_id,)) as cur:
        return await cur.fetchone()


async def claim_next_runner_job(db: aiosqlite.Connection, runner_id: str) -> aiosqlite.Row | None:
    """Atomically claim the oldest queued job assigned to this runner (a
    conditional UPDATE, so two concurrent polls from a hiccuping runner client
    never both claim the same job)."""
    async with db.execute(
        "SELECT id FROM workflow_runner_jobs WHERE runner_id = ? AND status = 'queued' "
        "ORDER BY created_at ASC LIMIT 1",
        (runner_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    cur = await db.execute(
        "UPDATE workflow_runner_jobs SET status = 'claimed', claimed_at = ? WHERE id = ? AND status = 'queued'",
        (_now(), row["id"]),
    )
    await db.commit()
    if not cur.rowcount:
        return None
    return await get_runner_job(db, row["id"])


async def finish_runner_job(
    db: aiosqlite.Connection, job_id: str, *, ok: bool, result: dict | None = None, error: str | None = None,
) -> bool:
    cur = await db.execute(
        "UPDATE workflow_runner_jobs SET status = ?, result_json = ?, error = ?, finished_at = ? "
        "WHERE id = ? AND status IN ('queued', 'claimed')",
        ("done" if ok else "failed", json.dumps(result) if result is not None else None, error, _now(), job_id),
    )
    await db.commit()
    return (cur.rowcount or 0) > 0


async def timeout_runner_job(db: aiosqlite.Connection, job_id: str) -> None:
    await db.execute(
        "UPDATE workflow_runner_jobs SET status = 'timeout', finished_at = ? "
        "WHERE id = ? AND status IN ('queued', 'claimed')",
        (_now(), job_id),
    )
    await db.commit()
