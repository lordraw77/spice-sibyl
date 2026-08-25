"""Aggregate metrics: per-workflow, per-node and per-prompt-variant.

Extracted from the former single-file graph_workflow_repository.py.
"""

import aiosqlite

from app.schemas.graph_workflows import WorkflowStatsOut


# ── stats (Phase 36 — roadmap fase 5.1; environment filter — Phase 39 fase 7.2) ─

async def workflow_stats_for_profile(
    db: aiosqlite.Connection, profile_id: str, *, environment: str | None = None,
) -> list[WorkflowStatsOut]:
    """Per-workflow aggregates: run counts by outcome, success rate over terminal
    runs, average terminal-run duration, and the LLM token totals summed from the
    `_usage` key that llm.* node outputs carry (json_extract over output_json).

    ``environment`` (Phase 39 — roadmap fase 7.2, extending fase 5.1) optionally
    scopes every aggregate to runs executed in that named environment — e.g.
    comparing `prod` health against the unfiltered (all-environments) totals.
    The join condition (not a WHERE filter) keeps every workflow in the result
    even when it has zero runs in the requested environment, matching the
    unfiltered endpoint's shape (0 runs, not an omitted row)."""
    env_clause = "" if environment is None else "AND r.environment = ?"
    env_params: list = [] if environment is None else [environment]

    async with db.execute(
        f"""
        SELECT w.id, w.name, w.active,
               COUNT(r.id)                                            AS runs,
               SUM(CASE WHEN r.status = 'completed' THEN 1 ELSE 0 END) AS completed,
               SUM(CASE WHEN r.status = 'failed'    THEN 1 ELSE 0 END) AS failed,
               SUM(CASE WHEN r.status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
               AVG(CASE WHEN r.status IN ('completed', 'failed')
                        THEN r.updated_at - r.created_at END)          AS avg_duration_s,
               MAX(r.created_at)                                       AS last_run_at
        FROM workflows w
        LEFT JOIN workflow_runs r ON r.workflow_id = w.id {env_clause}
        WHERE w.profile_id = ?
        GROUP BY w.id
        ORDER BY w.updated_at DESC
        """,  # noqa: S608 — env_clause is a fixed literal, never interpolated user input
        (*env_params, profile_id),
    ) as cur:
        rows = await cur.fetchall()

    async with db.execute(
        f"""
        SELECT r.workflow_id,
               SUM(COALESCE(json_extract(nr.output_json, '$._usage.tokens_in'), 0))    AS tokens_in,
               SUM(COALESCE(json_extract(nr.output_json, '$._usage.tokens_out'), 0))   AS tokens_out,
               SUM(COALESCE(json_extract(nr.output_json, '$._usage.tokens_total'), 0)) AS tokens_total
        FROM workflow_node_runs nr
        JOIN workflow_runs r ON r.id = nr.run_id
        JOIN workflows w ON w.id = r.workflow_id
        WHERE w.profile_id = ? AND nr.output_json LIKE '%_usage%' {env_clause}
        GROUP BY r.workflow_id
        """,  # noqa: S608 — env_clause is a fixed literal, never interpolated user input
        (profile_id, *env_params),
    ) as cur:
        tokens = {t["workflow_id"]: t for t in await cur.fetchall()}

    out: list[WorkflowStatsOut] = []
    for row in rows:
        completed = row["completed"] or 0
        failed = row["failed"] or 0
        terminal = completed + failed
        tok = tokens.get(row["id"])
        out.append(WorkflowStatsOut(
            workflow_id=row["id"],
            workflow_name=row["name"],
            active=bool(row["active"]),
            runs=row["runs"] or 0,
            completed=completed,
            failed=failed,
            cancelled=row["cancelled"] or 0,
            success_rate=(completed / terminal) if terminal else None,
            avg_duration_s=row["avg_duration_s"],
            tokens_in=int(tok["tokens_in"] or 0) if tok else 0,
            tokens_out=int(tok["tokens_out"] or 0) if tok else 0,
            tokens_total=int(tok["tokens_total"] or 0) if tok else 0,
            last_run_at=row["last_run_at"],
        ))
    return out


# ── per-node metrics (Phase 39 — roadmap fase 7.4) ──────────────────────────

def _percentile(sorted_values: list[float], q: float) -> float | None:
    """Nearest-rank percentile over an ascending list (None when empty)."""
    if not sorted_values:
        return None
    idx = min(len(sorted_values) - 1, max(0, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[idx]


async def node_stats_for_workflow(db: aiosqlite.Connection, workflow_id: str):
    """Per-node aggregates over the workflow's run history: executions by
    outcome, error rate, avg/p50/p95 duration and LLM tokens (from the
    ``_usage`` key of the node outputs). Percentiles are computed in Python —
    SQLite has no percentile aggregate — over at most the most recent rows the
    query returns, which is fine at the per-workflow scale this serves."""
    from app.schemas.graph_workflows import WorkflowNodeStatsOut

    async with db.execute(
        """
        SELECT nr.node_id, nr.node_type, nr.status, nr.started_at, nr.finished_at,
               COALESCE(json_extract(nr.output_json, '$._usage.tokens_total'), 0) AS tokens_total
        FROM workflow_node_runs nr
        JOIN workflow_runs r ON r.id = nr.run_id
        WHERE r.workflow_id = ?
        ORDER BY nr.started_at ASC
        """,
        (workflow_id,),
    ) as cur:
        rows = await cur.fetchall()

    acc: dict[str, dict] = {}
    for r in rows:
        entry = acc.setdefault(r["node_id"], {
            "node_type": r["node_type"], "executions": 0, "ok": 0, "error": 0,
            "skipped": 0, "durations": [], "tokens_total": 0, "last_executed_at": None,
        })
        entry["node_type"] = r["node_type"]
        entry["executions"] += 1
        if r["status"] in ("ok", "error", "skipped"):
            entry[r["status"]] += 1
        if r["status"] in ("ok", "error") and r["started_at"] and r["finished_at"]:
            entry["durations"].append(float(r["finished_at"] - r["started_at"]))
        try:
            entry["tokens_total"] += int(r["tokens_total"] or 0)
        except (TypeError, ValueError):
            pass
        if r["started_at"]:
            entry["last_executed_at"] = max(entry["last_executed_at"] or 0, r["started_at"])

    out = []
    for node_id, e in acc.items():
        durations = sorted(e["durations"])
        terminal = e["ok"] + e["error"]
        out.append(WorkflowNodeStatsOut(
            node_id=node_id,
            node_type=e["node_type"],
            executions=e["executions"],
            ok=e["ok"],
            error=e["error"],
            skipped=e["skipped"],
            error_rate=(e["error"] / terminal) if terminal else None,
            avg_duration_s=(sum(durations) / len(durations)) if durations else None,
            p50_duration_s=_percentile(durations, 0.50),
            p95_duration_s=_percentile(durations, 0.95),
            tokens_total=e["tokens_total"],
            last_executed_at=e["last_executed_at"],
        ))
    out.sort(key=lambda s: (-(s.error_rate or 0), -(s.p95_duration_s or 0)))
    return out


# ── prompt A/B variant metrics (Phase 50 — roadmap fase 18.2) ───────────────

async def variant_stats_for_node(db: aiosqlite.Connection, workflow_id: str, node_id: str):
    """Per-variant aggregates for one node over the workflow's run history: how
    each ``_variant`` performed (executions, ok/error, judge score, tokens). Runs
    without a recorded variant fall under ``(default)``. The leading variant
    (highest avg judge score, else highest ok-rate) is flagged ``winner`` so the
    editor can offer "promote variant". Reads the same node-run outputs as the
    fase 7.4 metrics — no extra bookkeeping."""
    from app.schemas.graph_workflows import WorkflowNodeVariantStatsOut

    async with db.execute(
        """
        SELECT nr.status,
               COALESCE(json_extract(nr.output_json, '$._variant'), '(default)') AS variant,
               json_extract(nr.output_json, '$.score')                AS score,
               json_extract(nr.output_json, '$.passed')               AS passed,
               COALESCE(json_extract(nr.output_json, '$._usage.tokens_total'), 0) AS tokens_total
        FROM workflow_node_runs nr
        JOIN workflow_runs r ON r.id = nr.run_id
        WHERE r.workflow_id = ? AND nr.node_id = ? AND nr.status IN ('ok', 'error')
        """,
        (workflow_id, node_id),
    ) as cur:
        rows = await cur.fetchall()

    acc: dict[str, dict] = {}
    for r in rows:
        e = acc.setdefault(r["variant"], {
            "executions": 0, "ok": 0, "error": 0, "scores": [], "passes": 0, "judged": 0, "tokens": [],
        })
        e["executions"] += 1
        if r["status"] in ("ok", "error"):
            e[r["status"]] += 1
        if isinstance(r["score"], (int, float)):
            e["scores"].append(float(r["score"]))
        if r["passed"] is not None:
            e["judged"] += 1
            if r["passed"]:
                e["passes"] += 1
        try:
            e["tokens"].append(int(r["tokens_total"] or 0))
        except (TypeError, ValueError):
            pass

    out = []
    for variant, e in acc.items():
        terminal = e["ok"] + e["error"]
        out.append(WorkflowNodeVariantStatsOut(
            variant=variant,
            executions=e["executions"],
            ok=e["ok"],
            error=e["error"],
            ok_rate=(e["ok"] / terminal) if terminal else None,
            avg_score=(sum(e["scores"]) / len(e["scores"])) if e["scores"] else None,
            pass_rate=(e["passes"] / e["judged"]) if e["judged"] else None,
            avg_tokens=(sum(e["tokens"]) / len(e["tokens"])) if e["tokens"] else None,
        ))
    # Winner: best avg judge score, else best ok-rate; needs ≥2 variants to matter.
    ranked = [s for s in out if s.variant != "(default)"] or out
    if ranked:
        best = max(ranked, key=lambda s: (
            s.avg_score if s.avg_score is not None else -1.0,
            s.ok_rate if s.ok_rate is not None else -1.0,
            s.executions,
        ))
        best.winner = True
    out.sort(key=lambda s: (-(s.avg_score or -1.0), -(s.ok_rate or -1.0)))
    return out
