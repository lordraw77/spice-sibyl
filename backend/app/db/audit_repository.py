"""Audit log persistence (Phase 13) — records who did what and when."""

import time
import uuid

import aiosqlite

from app.schemas.auth import AuditEntry


async def record(
    db: aiosqlite.Connection,
    user_id: str | None,
    action: str,
    resource: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
) -> None:
    """Best-effort audit write — never raises so it can't break the main action."""
    try:
        await db.execute(
            "INSERT INTO audit_log (id, user_id, action, resource, detail, ip, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, action, resource, detail, ip, int(time.time())),
        )
        await db.commit()
    except aiosqlite.Error:
        pass


async def list_entries(
    db: aiosqlite.Connection,
    limit: int = 200,
    user_id: str | None = None,
) -> list[AuditEntry]:
    if user_id:
        query = "SELECT * FROM audit_log WHERE user_id = ? ORDER BY created_at DESC LIMIT ?"
        params: tuple = (user_id, limit)
    else:
        query = "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?"
        params = (limit,)
    async with db.execute(query, params) as cursor:
        rows = await cursor.fetchall()
    return [
        AuditEntry(
            id=r["id"],
            user_id=r["user_id"],
            action=r["action"],
            resource=r["resource"],
            detail=r["detail"],
            ip=r["ip"],
            created_at=r["created_at"],
        )
        for r in rows
    ]
