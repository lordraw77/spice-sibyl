"""
Phase 18 — persistent workflows repository.

CRUD over ``agent_runs`` + ``agent_run_steps``. The run's serialized message
history (``messages``, JSON) is the resume checkpoint; steps are the
append-only human-readable trace shown in the UI.
"""

import time
import uuid

import aiosqlite

from app.schemas.workflows import AgentRunOut, AgentRunStep


def _row_to_run(row: aiosqlite.Row) -> AgentRunOut:
    return AgentRunOut(
        id=row["id"],
        profile_id=row["profile_id"],
        goal=row["goal"],
        model=row["model"],
        status=row["status"],
        max_steps=row["max_steps"],
        current_step=row["current_step"],
        result=row["result"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_step(row: aiosqlite.Row) -> AgentRunStep:
    return AgentRunStep(
        id=row["id"],
        run_id=row["run_id"],
        step_index=row["step_index"],
        kind=row["kind"],
        name=row["name"],
        content=row["content"],
        created_at=row["created_at"],
    )


async def create_run(
    db: aiosqlite.Connection, profile_id: str, goal: str, model: str, max_steps: int
) -> AgentRunOut:
    now = int(time.time())
    run_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO agent_runs (id, profile_id, goal, model, status, max_steps, "
        "current_step, created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', ?, 0, ?, ?)",
        (run_id, profile_id, goal, model, max_steps, now, now),
    )
    await db.commit()
    return await get_run(db, run_id)  # type: ignore[return-value]


async def get_run(db: aiosqlite.Connection, run_id: str) -> AgentRunOut | None:
    async with db.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)) as cursor:
        row = await cursor.fetchone()
    return _row_to_run(row) if row else None


async def list_runs(db: aiosqlite.Connection, profile_id: str) -> list[AgentRunOut]:
    async with db.execute(
        "SELECT * FROM agent_runs WHERE profile_id = ? ORDER BY updated_at DESC",
        (profile_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_run(r) for r in rows]


async def get_status(db: aiosqlite.Connection, run_id: str) -> str | None:
    async with db.execute(
        "SELECT status FROM agent_runs WHERE id = ?", (run_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return row["status"] if row else None


async def set_status(
    db: aiosqlite.Connection,
    run_id: str,
    status: str,
    *,
    result: str | None = None,
    error: str | None = None,
) -> None:
    await db.execute(
        "UPDATE agent_runs SET status = ?, result = COALESCE(?, result), "
        "error = ?, updated_at = ? WHERE id = ?",
        (status, result, error, int(time.time()), run_id),
    )
    await db.commit()


async def checkpoint(
    db: aiosqlite.Connection, run_id: str, messages_json: str, current_step: int
) -> None:
    """Persist the serialized message history after an iteration (resume point)."""
    await db.execute(
        "UPDATE agent_runs SET messages = ?, current_step = ?, updated_at = ? WHERE id = ?",
        (messages_json, current_step, int(time.time()), run_id),
    )
    await db.commit()


async def get_messages_json(db: aiosqlite.Connection, run_id: str) -> str | None:
    async with db.execute(
        "SELECT messages FROM agent_runs WHERE id = ?", (run_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return row["messages"] if row else None


async def delete_run(db: aiosqlite.Connection, run_id: str) -> bool:
    cursor = await db.execute("DELETE FROM agent_runs WHERE id = ?", (run_id,))
    await db.commit()
    return cursor.rowcount > 0


async def add_step(
    db: aiosqlite.Connection,
    run_id: str,
    step_index: int,
    kind: str,
    content: str,
    name: str | None = None,
) -> AgentRunStep:
    step_id = str(uuid.uuid4())
    now = int(time.time())
    await db.execute(
        "INSERT INTO agent_run_steps (id, run_id, step_index, kind, name, content, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (step_id, run_id, step_index, kind, name, content, now),
    )
    await db.commit()
    return AgentRunStep(
        id=step_id, run_id=run_id, step_index=step_index, kind=kind,
        name=name, content=content, created_at=now,
    )


async def list_steps(db: aiosqlite.Connection, run_id: str) -> list[AgentRunStep]:
    async with db.execute(
        "SELECT * FROM agent_run_steps WHERE run_id = ? ORDER BY step_index, created_at, id",
        (run_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_step(r) for r in rows]
