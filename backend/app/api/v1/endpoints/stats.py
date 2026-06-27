from fastapi import APIRouter, Depends, Query
import aiosqlite

from app.core.config import settings
from app.db.database import get_db
from app.db.stats_repository import get_daily_stats, get_usage_stats
from app.dependencies.auth import resolve_profile
from app.schemas.stats import DailyStats, TelegramStats, UsageStats

router = APIRouter()


@router.get("", response_model=UsageStats)
async def usage_stats(
    db: aiosqlite.Connection = Depends(get_db),
) -> UsageStats:
    result = await get_usage_stats(db)

    tg_enabled = bool(settings.telegram_bot_token)
    if tg_enabled:
        from app.telegram.bot import get_telegram_stats
        tg = get_telegram_stats()
        result.telegram = TelegramStats(enabled=True, **tg)
    else:
        result.telegram = TelegramStats(enabled=False)

    return result


@router.get("/daily", response_model=list[DailyStats])
async def daily_stats(
    days: int = Query(default=30, ge=1, le=365),
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    return await get_daily_stats(db, days, profile_id)
