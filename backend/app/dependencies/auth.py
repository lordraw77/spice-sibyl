"""
FastAPI auth dependencies (Phase 13).

  * get_current_user — decode the Bearer access token, load the user, 401 on failure.
                       Also stashes user id on request.state for the rate limiter.
  * require_role     — factory yielding a dependency that enforces a role allowlist.
  * block_read_only  — reject mutating methods for the 'read-only' role.
  * resolve_profile  — validate the X-Profile-ID header belongs to the caller, lazily
                       provisioning a default profile so data stays per-user isolated.
"""

import aiosqlite
from fastapi import Depends, Header, HTTPException, Request

from app.db import profile_repository, user_repository
from app.db.database import get_db
from app.schemas.auth import UserOut
from app.services import auth_service

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: aiosqlite.Connection = Depends(get_db),
) -> UserOut:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization[7:].strip()
    claims = auth_service.decode_token(token)
    if not claims or claims.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    row = await user_repository.get_by_id(db, claims["sub"])
    if not row or row["disabled"]:
        raise HTTPException(status_code=401, detail="User not found or disabled")

    user = UserOut(
        id=row["id"],
        email=row["email"],
        role=row["role"],
        disabled=bool(row["disabled"]),
        created_at=row["created_at"],
    )
    # Expose identity for the slowapi key function.
    request.state.user_id = user.id
    return user


def require_role(*roles: str):
    async def _checker(user: UserOut = Depends(get_current_user)) -> UserOut:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return _checker


async def block_read_only(
    request: Request, user: UserOut = Depends(get_current_user)
) -> UserOut:
    if user.role == "read-only" and request.method in _MUTATING_METHODS:
        raise HTTPException(status_code=403, detail="Read-only account")
    return user


async def resolve_profile(
    x_profile_id: str | None = Header(default=None),
    user: UserOut = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> str:
    """
    Return a profile_id guaranteed to belong to the caller.

    A supplied X-Profile-ID must be owned by the user (else 403).  When absent,
    fall back to the user's first profile, creating one on demand so a new
    account is never silently scoped to another user's data.
    """
    if x_profile_id:
        profile = await profile_repository.get_profile(db, x_profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        if profile.user_id != user.id:
            raise HTTPException(status_code=403, detail="Profile does not belong to you")
        return x_profile_id

    owned = await profile_repository.list_profiles(db, user_id=user.id)
    if owned:
        return owned[0].id
    created = await profile_repository.create_profile(db, user.email, user_id=user.id)
    return created.id
