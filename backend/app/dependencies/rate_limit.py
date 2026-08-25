"""
Per-user sliding-window rate limiter (Phase 13) + login guard (audit 2.5).

``rate_limit`` is keyed by the authenticated user id (set on request.state by
get_current_user), so it stays correct behind the nginx reverse proxy where
every request shares the proxy's IP.

That dependency on ``get_current_user`` is also why it could never protect
``/auth/login`` and ``/auth/refresh``: there *is* no authenticated user yet, so
the public routes were left with no limit at all and brute force was free.
``login_guard`` is the independent limiter for them — keyed by client IP and,
on login, by the submitted email, with a lockout that lengthens as failures
accumulate.

The windows live behind ``services.rate_limiting``: in memory by default, or
shared across instances through the database when RATE_LIMIT_BACKEND=database
(roadmap v2 § 3, P2).
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


# ── Login guard (audit 2.5) ──────────────────────────────────────────────────

def _client_ip(request: Request) -> str:
    """Real client IP behind the reverse proxy, falling back to the socket peer.

    X-Forwarded-For is only trusted when the deployment says it is: taking the
    header blindly would let an attacker rotate the throttling key at will by
    forging it, which is worse than one shared key for everyone behind a proxy.
    """
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


_AUTH_MAX, _AUTH_WINDOW = _parse(settings.rate_limit_auth)

#: Progressive lockout: (window seconds, failures tolerated in it). Checked in
#: order, so the first tier that is full wins. Failing a few times in a minute
#: is a typo; failing thirty times in an hour is a dictionary.
_LOCKOUT_TIERS: tuple[tuple[float, int], ...] = (
    (60.0, 5),
    (900.0, 15),
    (3600.0, 30),
)


def _fail_bucket(email: str) -> str:
    return f"login-fail:{email.strip().lower()}"


async def _assert_not_locked_out(email: str) -> None:
    limiter = get_limiter()
    bucket = _fail_bucket(email)
    for window, tolerated in _LOCKOUT_TIERS:
        if await limiter.count(bucket, window) >= tolerated:
            raise HTTPException(
                status_code=429,
                detail="Too many failed login attempts. Try again later.",
                headers={"Retry-After": str(int(window))},
            )


async def record_login_failure(email: str) -> None:
    """Count one failed attempt against the email's lockout tiers."""
    await get_limiter().record(_fail_bucket(email))


async def _throttle(bucket: str) -> None:
    retry_after = await get_limiter().try_admit(bucket, _AUTH_MAX, _AUTH_WINDOW)
    if retry_after is None:
        return
    raise HTTPException(
        status_code=429,
        detail="Too many authentication attempts",
        headers={"Retry-After": str(max(int(math.ceil(retry_after)), 1))},
    )


async def login_guard(request: Request) -> None:
    """Rate limit an unauthenticated auth attempt by IP (and email, on login).

    Reads the JSON body directly rather than declaring a Pydantic parameter so
    the same dependency covers both /login (has an email) and /refresh (does
    not), and so a malformed body is throttled instead of sailing past the guard
    on its way to a 422.
    """
    await _throttle(f"auth-ip:{_client_ip(request)}")

    email = None
    try:
        body = await request.json()
        if isinstance(body, dict):
            email = body.get("email")
    except Exception:  # noqa: BLE001 — no body, or not JSON: IP throttling stands
        return

    if isinstance(email, str) and email.strip():
        await _throttle(f"auth-email:{email.strip().lower()}")
        await _assert_not_locked_out(email)
