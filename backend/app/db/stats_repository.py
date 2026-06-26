from collections import defaultdict

import aiosqlite

import time

from app.schemas.stats import (
    DailyStats,
    GlobalStats,
    ModelStats,
    ProfileSlice,
    ProfileSummary,
    ProviderStats,
    UsageStats,
)


async def get_usage_stats(db: aiosqlite.Connection) -> UsageStats:

    # ── Global totals ────────────────────────────────────────────
    async with db.execute(
        """
        SELECT
            COUNT(DISTINCT c.id)                   AS total_conversations,
            COUNT(m.id)                            AS total_messages,
            COALESCE(SUM(m.prompt_tokens), 0)      AS total_prompt_tokens,
            COALESCE(SUM(m.completion_tokens), 0)  AS total_completion_tokens,
            COALESCE(SUM(m.total_tokens), 0)       AS total_tokens,
            COALESCE(SUM(m.estimated_cost), 0.0)   AS total_cost
        FROM messages m
        JOIN conversations c ON m.conversation_id = c.id
        WHERE m.role = 'assistant'
        """
    ) as cursor:
        row = await cursor.fetchone()

    global_stats = GlobalStats(
        total_conversations=row["total_conversations"] or 0,
        total_messages=row["total_messages"] or 0,
        total_prompt_tokens=row["total_prompt_tokens"] or 0,
        total_completion_tokens=row["total_completion_tokens"] or 0,
        total_tokens=row["total_tokens"] or 0,
        total_cost=row["total_cost"] or 0.0,
    )

    # ── Per-profile summary ──────────────────────────────────────
    async with db.execute(
        """
        SELECT
            p.id                                       AS profile_id,
            p.name                                     AS profile_name,
            COUNT(DISTINCT c.id)                       AS total_conversations,
            COUNT(m.id)                                AS total_messages,
            COALESCE(SUM(m.prompt_tokens), 0)          AS total_prompt_tokens,
            COALESCE(SUM(m.completion_tokens), 0)      AS total_completion_tokens,
            COALESCE(SUM(m.total_tokens), 0)           AS total_tokens,
            COALESCE(SUM(m.estimated_cost), 0.0)       AS total_cost
        FROM profiles p
        LEFT JOIN conversations c ON c.profile_id = p.id
        LEFT JOIN messages m
               ON m.conversation_id = c.id AND m.role = 'assistant'
        GROUP BY p.id, p.name
        ORDER BY total_tokens DESC
        """
    ) as cursor:
        profile_rows = await cursor.fetchall()

    by_profile = [
        ProfileSummary(
            profile_id=r["profile_id"],
            profile_name=r["profile_name"],
            total_conversations=r["total_conversations"] or 0,
            total_messages=r["total_messages"] or 0,
            total_prompt_tokens=r["total_prompt_tokens"] or 0,
            total_completion_tokens=r["total_completion_tokens"] or 0,
            total_tokens=r["total_tokens"] or 0,
            total_cost=r["total_cost"] or 0.0,
        )
        for r in profile_rows
    ]

    # ── Per-provider per-profile breakdown ───────────────────────
    async with db.execute(
        """
        SELECT
            m.provider,
            c.profile_id,
            COALESCE(p.name, c.profile_id)         AS profile_name,
            COUNT(*)                               AS message_count,
            COALESCE(SUM(m.prompt_tokens), 0)      AS prompt_tokens,
            COALESCE(SUM(m.completion_tokens), 0)  AS completion_tokens,
            COALESCE(SUM(m.total_tokens), 0)       AS total_tokens,
            COALESCE(SUM(m.estimated_cost), 0.0)   AS estimated_cost
        FROM messages m
        JOIN conversations c ON m.conversation_id = c.id
        LEFT JOIN profiles p ON p.id = c.profile_id
        WHERE m.role = 'assistant'
        GROUP BY m.provider, c.profile_id, p.name
        ORDER BY m.provider, total_tokens DESC
        """
    ) as cursor:
        prov_profile_rows = await cursor.fetchall()

    # Group per-profile slices by provider key
    _prov_slices: dict[str | None, list[ProfileSlice]] = defaultdict(list)
    for r in prov_profile_rows:
        _prov_slices[r["provider"]].append(
            ProfileSlice(
                profile_id=r["profile_id"],
                profile_name=r["profile_name"],
                message_count=r["message_count"],
                prompt_tokens=r["prompt_tokens"],
                completion_tokens=r["completion_tokens"],
                total_tokens=r["total_tokens"],
                estimated_cost=r["estimated_cost"],
            )
        )

    # ── Per-provider totals ──────────────────────────────────────
    async with db.execute(
        """
        SELECT
            m.provider,
            COUNT(*)                               AS message_count,
            COALESCE(SUM(m.prompt_tokens), 0)      AS prompt_tokens,
            COALESCE(SUM(m.completion_tokens), 0)  AS completion_tokens,
            COALESCE(SUM(m.total_tokens), 0)       AS total_tokens,
            COALESCE(SUM(m.estimated_cost), 0.0)   AS estimated_cost,
            AVG(m.latency_ms)                      AS avg_latency_ms,
            AVG(m.tokens_per_second)               AS avg_tokens_per_second
        FROM messages m
        JOIN conversations c ON m.conversation_id = c.id
        WHERE m.role = 'assistant'
        GROUP BY m.provider
        ORDER BY total_tokens DESC
        """
    ) as cursor:
        provider_rows = await cursor.fetchall()

    by_provider = [
        ProviderStats(
            provider=r["provider"],
            message_count=r["message_count"],
            prompt_tokens=r["prompt_tokens"],
            completion_tokens=r["completion_tokens"],
            total_tokens=r["total_tokens"],
            estimated_cost=r["estimated_cost"],
            avg_latency_ms=r["avg_latency_ms"],
            avg_tokens_per_second=r["avg_tokens_per_second"],
            by_profile=_prov_slices.get(r["provider"], []),
        )
        for r in provider_rows
    ]

    # ── Per-model per-profile breakdown ──────────────────────────
    async with db.execute(
        """
        SELECT
            m.model,
            m.provider,
            c.profile_id,
            COALESCE(p.name, c.profile_id)         AS profile_name,
            COUNT(*)                               AS message_count,
            COALESCE(SUM(m.prompt_tokens), 0)      AS prompt_tokens,
            COALESCE(SUM(m.completion_tokens), 0)  AS completion_tokens,
            COALESCE(SUM(m.total_tokens), 0)       AS total_tokens,
            COALESCE(SUM(m.estimated_cost), 0.0)   AS estimated_cost
        FROM messages m
        JOIN conversations c ON m.conversation_id = c.id
        LEFT JOIN profiles p ON p.id = c.profile_id
        WHERE m.role = 'assistant'
        GROUP BY m.model, m.provider, c.profile_id, p.name
        ORDER BY m.model, m.provider, total_tokens DESC
        """
    ) as cursor:
        model_profile_rows = await cursor.fetchall()

    # Group per-profile slices by (model, provider) key
    _model_slices: dict[tuple, list[ProfileSlice]] = defaultdict(list)
    for r in model_profile_rows:
        key = (r["model"], r["provider"])
        _model_slices[key].append(
            ProfileSlice(
                profile_id=r["profile_id"],
                profile_name=r["profile_name"],
                message_count=r["message_count"],
                prompt_tokens=r["prompt_tokens"],
                completion_tokens=r["completion_tokens"],
                total_tokens=r["total_tokens"],
                estimated_cost=r["estimated_cost"],
            )
        )

    # ── Per-model totals ─────────────────────────────────────────
    async with db.execute(
        """
        SELECT
            m.model,
            m.provider,
            COUNT(*)                               AS message_count,
            COALESCE(SUM(m.prompt_tokens), 0)      AS prompt_tokens,
            COALESCE(SUM(m.completion_tokens), 0)  AS completion_tokens,
            COALESCE(SUM(m.total_tokens), 0)       AS total_tokens,
            COALESCE(SUM(m.estimated_cost), 0.0)   AS estimated_cost,
            AVG(m.latency_ms)                      AS avg_latency_ms,
            AVG(m.tokens_per_second)               AS avg_tokens_per_second
        FROM messages m
        JOIN conversations c ON m.conversation_id = c.id
        WHERE m.role = 'assistant'
        GROUP BY m.model, m.provider
        ORDER BY total_tokens DESC
        """
    ) as cursor:
        model_rows = await cursor.fetchall()

    by_model = [
        ModelStats(
            model=r["model"],
            provider=r["provider"],
            message_count=r["message_count"],
            prompt_tokens=r["prompt_tokens"],
            completion_tokens=r["completion_tokens"],
            total_tokens=r["total_tokens"],
            estimated_cost=r["estimated_cost"],
            avg_latency_ms=r["avg_latency_ms"],
            avg_tokens_per_second=r["avg_tokens_per_second"],
            by_profile=_model_slices.get((r["model"], r["provider"]), []),
        )
        for r in model_rows
    ]

    return UsageStats(
        global_stats=global_stats,
        by_profile=by_profile,
        by_provider=by_provider,
        by_model=by_model,
    )


async def get_daily_stats(
    db: aiosqlite.Connection, days: int = 30, profile_id: str | None = None
) -> list[DailyStats]:
    cutoff = int(time.time()) - (days * 86400)
    query = """
    SELECT
        date(m.created_at, 'unixepoch') AS day,
        COUNT(*)                        AS message_count,
        COALESCE(SUM(m.prompt_tokens), 0)     AS prompt_tokens,
        COALESCE(SUM(m.completion_tokens), 0) AS completion_tokens,
        COALESCE(SUM(m.total_tokens), 0)      AS total_tokens,
        COALESCE(SUM(m.estimated_cost), 0)    AS estimated_cost
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    WHERE m.role = 'assistant'
      AND m.created_at > ?
    """
    params: list = [cutoff]
    if profile_id:
        query += " AND c.profile_id = ?"
        params.append(profile_id)
    query += " GROUP BY day ORDER BY day ASC"

    async with db.execute(query, params) as cursor:
        rows = await cursor.fetchall()
    return [
        DailyStats(
            date=r["day"],
            message_count=r["message_count"],
            prompt_tokens=r["prompt_tokens"],
            completion_tokens=r["completion_tokens"],
            total_tokens=r["total_tokens"],
            estimated_cost=r["estimated_cost"],
        )
        for r in rows
    ]
