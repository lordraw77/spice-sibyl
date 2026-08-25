"""Token/run budgets, quota bookkeeping and run retention.

Extracted from the former single-file graph_workflow_repository.py.
"""

import aiosqlite

from ._common import _now


# ── budgets and quotas (Phase 44 — roadmap fase 12.1) ───────────────────────

async def workflow_usage_for_period(db: aiosqlite.Connection, workflow_id: str, period_start: int) -> dict:
    """Runs and total LLM tokens for one workflow since ``period_start`` (a
    calendar-month epoch) — the same sources as the fase 5.1 stats, just
    time-boxed instead of lifetime, so budgets can be checked cheaply without a
    duplicated usage counter (reset "for free" as the period rolls over)."""
    async with db.execute(
        "SELECT COUNT(*) AS runs FROM workflow_runs WHERE workflow_id = ? AND created_at >= ?",
        (workflow_id, period_start),
    ) as cur:
        runs = (await cur.fetchone())["runs"] or 0
    async with db.execute(
        """
        SELECT SUM(COALESCE(json_extract(nr.output_json, '$._usage.tokens_total'), 0)) AS tokens_total
        FROM workflow_node_runs nr
        JOIN workflow_runs r ON r.id = nr.run_id
        WHERE r.workflow_id = ? AND r.created_at >= ? AND nr.output_json LIKE '%_usage%'
        """,
        (workflow_id, period_start),
    ) as cur:
        tokens_total = (await cur.fetchone())["tokens_total"] or 0
    return {"runs": int(runs), "tokens_total": int(tokens_total)}


async def profile_usage_for_period(db: aiosqlite.Connection, profile_id: str, period_start: int) -> dict:
    """Same as :func:`workflow_usage_for_period`, summed over every workflow
    owned by ``profile_id`` — the profile-wide ("workspace") usage."""
    async with db.execute(
        "SELECT COUNT(*) AS runs FROM workflow_runs WHERE profile_id = ? AND created_at >= ?",
        (profile_id, period_start),
    ) as cur:
        runs = (await cur.fetchone())["runs"] or 0
    async with db.execute(
        """
        SELECT SUM(COALESCE(json_extract(nr.output_json, '$._usage.tokens_total'), 0)) AS tokens_total
        FROM workflow_node_runs nr
        JOIN workflow_runs r ON r.id = nr.run_id
        WHERE r.profile_id = ? AND r.created_at >= ? AND nr.output_json LIKE '%_usage%'
        """,
        (profile_id, period_start),
    ) as cur:
        tokens_total = (await cur.fetchone())["tokens_total"] or 0
    return {"runs": int(runs), "tokens_total": int(tokens_total)}


async def set_workflow_budget_warned(db: aiosqlite.Connection, workflow_id: str, period: str) -> None:
    await db.execute(
        "UPDATE workflows SET budget_warned_period = ? WHERE id = ?", (period, workflow_id)
    )
    await db.commit()


async def get_profile_budget(db: aiosqlite.Connection, profile_id: str) -> dict | None:
    async with db.execute(
        "SELECT * FROM profile_budgets WHERE profile_id = ?", (profile_id,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return {
        "profile_id": row["profile_id"],
        "token_budget_month": row["token_budget_month"],
        "run_budget_month": row["run_budget_month"],
        "warned_period": row["warned_period"],
    }


async def set_profile_budget(
    db: aiosqlite.Connection, profile_id: str,
    token_budget_month: int | None, run_budget_month: int | None,
) -> None:
    await db.execute(
        "INSERT INTO profile_budgets (profile_id, token_budget_month, run_budget_month, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(profile_id) DO UPDATE SET "
        "token_budget_month = excluded.token_budget_month, "
        "run_budget_month = excluded.run_budget_month, "
        "updated_at = excluded.updated_at",
        (profile_id, token_budget_month, run_budget_month, _now()),
    )
    await db.commit()


async def set_profile_budget_warned(db: aiosqlite.Connection, profile_id: str, period: str) -> None:
    await db.execute(
        "UPDATE profile_budgets SET warned_period = ? WHERE profile_id = ?", (period, profile_id)
    )
    await db.commit()


# ── run retention (Phase 44 — roadmap fase 12.2) ────────────────────────────

async def purge_old_runs(db: aiosqlite.Connection, default_days: int, now: int) -> int:
    """Delete terminal runs (completed/failed/cancelled — never queued/pending/
    running/waiting/paused) older than the workflow's own ``runs_retention_days``
    override, falling back to ``default_days`` (0 disables purging for a
    workflow). ``workflow_node_runs`` cascade via the FK. Returns rows deleted."""
    if default_days <= 0:
        clause_default_disabled = "AND w.runs_retention_days IS NOT NULL AND w.runs_retention_days > 0"
    else:
        clause_default_disabled = ""
    cur = await db.execute(
        f"""
        DELETE FROM workflow_runs
        WHERE id IN (
            SELECT r.id FROM workflow_runs r
            JOIN workflows w ON w.id = r.workflow_id
            WHERE r.status IN ('completed', 'failed', 'cancelled')
              {clause_default_disabled}
              AND COALESCE(w.runs_retention_days, ?) > 0
              AND r.created_at < (? - COALESCE(w.runs_retention_days, ?) * 86400)
        )
        """,  # noqa: S608 — clause_default_disabled is a fixed literal, never interpolated user input
        (default_days, now, default_days),
    )
    await db.commit()
    return cur.rowcount or 0
