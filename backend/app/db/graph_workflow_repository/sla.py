"""SLA monitors: overlong runs and missed schedule beats (Phase 49).

Extracted from the former single-file graph_workflow_repository.py.
"""

import json

import aiosqlite


# ── Phase 49 (roadmap fase 17.2) — SLA monitors ─────────────────────────────

async def list_runs_over_duration(
    db: aiosqlite.Connection, now_ts: int
) -> list[dict]:
    """Runs that breached their workflow's ``sla.max_duration_s`` and have not yet
    raised the one-time alert (``sla_alerted = 0``). A *running* run counts once
    ``now - created_at`` exceeds the threshold; a terminal run once its total
    elapsed (``updated_at - created_at``) did. Joined to the workflow's SLA config
    and name so the sweep can alert and mark without a second query."""
    async with db.execute(
        "SELECT r.id, r.workflow_id, r.profile_id, r.status, r.created_at, r.updated_at, "
        "w.name AS workflow_name, w.sla_json "
        "FROM workflow_runs r JOIN workflows w ON w.id = r.workflow_id "
        "WHERE r.sla_alerted = 0 AND w.sla_json IS NOT NULL AND w.sla_json <> '{}' "
        "AND r.status IN ('running','pending','completed','failed')",
    ) as cur:
        rows = await cur.fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            sla = json.loads(r["sla_json"] or "{}")
        except (ValueError, TypeError):
            continue
        max_s = int(sla.get("max_duration_s") or 0)
        if max_s <= 0:
            continue
        if r["status"] in ("running", "pending"):
            elapsed = now_ts - r["created_at"]
        else:
            elapsed = (r["updated_at"] or r["created_at"]) - r["created_at"]
        if elapsed > max_s:
            d = dict(r)
            d["sla"] = sla
            d["elapsed_s"] = elapsed
            out.append(d)
    return out


async def mark_run_sla_alerted(db: aiosqlite.Connection, run_id: str) -> None:
    await db.execute("UPDATE workflow_runs SET sla_alerted = 1 WHERE id = ?", (run_id,))
    await db.commit()


async def list_overdue_schedule_triggers(db: aiosqlite.Connection, now_ts: int) -> list[dict]:
    """Fase 17.2 (missed beat) — enabled schedule triggers whose ``next_run_at``
    is set but so far in the past that the run never started (workflow inactive,
    scheduler was down, firing wedged). The per-workflow ``sla.missed_grace_s``
    (or 0 = disabled) sets how overdue is "missed"; ``last_sla_alert_at`` dedups
    so one miss raises one alert. Joined to workflow name/sla/active."""
    async with db.execute(
        "SELECT t.id, t.workflow_id, t.next_run_at, t.last_sla_alert_at, "
        "w.profile_id AS wf_profile_id, w.name AS workflow_name, w.sla_json, w.active AS wf_active "
        "FROM workflow_triggers t JOIN workflows w ON w.id = t.workflow_id "
        "WHERE t.type = 'schedule' AND t.enabled = 1 AND t.next_run_at IS NOT NULL",
    ) as cur:
        rows = await cur.fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            sla = json.loads(r["sla_json"] or "{}")
        except (ValueError, TypeError):
            continue
        grace = int(sla.get("missed_grace_s") or 0)
        if grace <= 0:
            continue
        if now_ts <= r["next_run_at"] + grace:
            continue
        # Dedup: only alert once per missed beat (last alert older than the beat).
        if r["last_sla_alert_at"] and r["last_sla_alert_at"] >= r["next_run_at"]:
            continue
        d = dict(r)
        d["sla"] = sla
        d["overdue_s"] = now_ts - r["next_run_at"]
        out.append(d)
    return out


async def mark_trigger_sla_alerted(db: aiosqlite.Connection, trigger_id: str, when: int) -> None:
    await db.execute(
        "UPDATE workflow_triggers SET last_sla_alert_at = ? WHERE id = ?", (when, trigger_id)
    )
    await db.commit()
