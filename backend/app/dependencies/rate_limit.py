"""
Per-user sliding-window rate limiter (Phase 13).

Keyed by the authenticated user id (set on request.state by get_current_user),
so it stays correct behind the nginx reverse proxy where every request shares
the proxy's IP.  In-memory only — adequate for a single process; a shared store
(Redis) would be needed for multi-worker deployments (noted for Phase 16).
"""

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

from app.core.config import settings
from app.dependencies.auth import get_current_user
from app.schemas.auth import UserOut

_UNITS = {"second": 1, "minute": 60, "hour": 3600}


def _parse(spec: str) -> tuple[int, int]:
    """Parse 'N/unit' (e.g. '60/minute') into (max_requests, window_seconds)."""
    try:
        count_s, unit = spec.split("/")
        return int(count_s), _UNITS[unit.strip().lower()]
    except (ValueError, KeyError):
        return 60, 60


_MAX, _WINDOW = _parse(settings.rate_limit_default)

# user_id → deque of recent request timestamps
_hits: dict[str, deque] = defaultdict(deque)


async def rate_limit(
    request: Request, user: UserOut = Depends(get_current_user)
) -> None:
    now = time.monotonic()
    bucket = _hits[user.id]
    cutoff = now - _WINDOW
    while bucket and bucket[0] < cutoff:
        bucket.popleft()

    if len(bucket) >= _MAX:
        retry_after = int(bucket[0] + _WINDOW - now) + 1
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(max(retry_after, 1))},
        )
    bucket.append(now)
