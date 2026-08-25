"""Workflow test suites (Phase 43).

Extracted from the former single-file graph_workflow_repository.py.
"""

import json
import uuid

import aiosqlite

from ._common import _now


# ── test cases (Phase 43 — roadmap fase 11.1) ───────────────────────────────

def _row_to_test_case(row: aiosqlite.Row) -> "WorkflowTestCaseOut":
    from app.schemas.graph_workflows import WorkflowTestCaseOut

    return WorkflowTestCaseOut(
        id=row["id"],
        workflow_id=row["workflow_id"],
        name=row["name"],
        trigger_payload=json.loads(row["trigger_payload_json"] or "{}"),
        assertions=json.loads(row["assertions_json"] or "[]"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def create_test_case(
    db: aiosqlite.Connection, workflow_id: str, name: str,
    trigger_payload: dict, assertions: list[dict],
) -> str:
    case_id = str(uuid.uuid4())
    now = _now()
    await db.execute(
        "INSERT INTO workflow_test_cases "
        "(id, workflow_id, name, trigger_payload_json, assertions_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (case_id, workflow_id, name, json.dumps(trigger_payload), json.dumps(assertions), now, now),
    )
    await db.commit()
    return case_id


async def list_test_cases(db: aiosqlite.Connection, workflow_id: str) -> list["WorkflowTestCaseOut"]:
    async with db.execute(
        "SELECT * FROM workflow_test_cases WHERE workflow_id = ? ORDER BY created_at", (workflow_id,)
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_test_case(r) for r in rows]


async def get_test_case(db: aiosqlite.Connection, case_id: str) -> "WorkflowTestCaseOut | None":
    async with db.execute("SELECT * FROM workflow_test_cases WHERE id = ?", (case_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_test_case(row) if row else None


async def update_test_case(
    db: aiosqlite.Connection, case_id: str, name: str,
    trigger_payload: dict, assertions: list[dict],
) -> bool:
    cur = await db.execute(
        "UPDATE workflow_test_cases SET name = ?, trigger_payload_json = ?, assertions_json = ?, updated_at = ? "
        "WHERE id = ?",
        (name, json.dumps(trigger_payload), json.dumps(assertions), _now(), case_id),
    )
    await db.commit()
    return cur.rowcount > 0


async def delete_test_case(db: aiosqlite.Connection, case_id: str) -> bool:
    cur = await db.execute("DELETE FROM workflow_test_cases WHERE id = ?", (case_id,))
    await db.commit()
    return cur.rowcount > 0
