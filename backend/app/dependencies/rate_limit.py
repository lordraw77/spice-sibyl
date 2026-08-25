"""
Per-user sliding-window rate limiter (Phase 13).

Keyed by the authenticated user id (set on request.state by get_current_user),
so it stays correct behind the nginx reverse proxy where every request shares
the proxy's IP.

The window itself lives behind ``services.rate_limiting``: in memory by
default, or shared across instances through the database when
RATE_LIMIT_BACKEND=database (roadmap v2 § 3, P2).
"""

import math

from fastapi import Depends, HTTPException, Request

from app.core.config import settings
from app.dependencies.auth import get_current_user
from app.schemas.auth import UserOut
from app.services.rate_limiting import get_limiter

_UNITS = {"second": 1, "minute": 60, "hour": 3600}


def _parse(spec: str) -> tuple[int, int]:
    """Parse 'N/unit' (e.g. '60/minute') into (max_requests, window_seconds)."""
    try:
        count_s, unit = spec.split("/")
        return int(count_s), _UNITS[unit.strip().lower()]
    except (ValueError, KeyError):
        return 60, 60


_MAX, _WINDOW = _parse(settings.rate_limit_default)


async def rate_limit(
    request: Request, user: UserOut = Depends(get_current_user)
) -> None:
    retry_after = await get_limiter().try_admit(f"user:{user.id}", _MAX, _WINDOW)
    if retry_after is None:
        return
    raise HTTPException(
        status_code=429,
        detail="Rate limit exceeded",
        headers={"Retry-After": str(max(int(math.ceil(retry_after)), 1))},
    )
