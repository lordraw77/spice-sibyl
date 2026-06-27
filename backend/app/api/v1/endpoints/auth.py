"""
Authentication & user-management endpoints (Phase 13).

Public:
  POST /v1/auth/login     — email/password → access + refresh tokens
  POST /v1/auth/refresh   — rotate a refresh token
Authenticated:
  POST /v1/auth/logout    — revoke the supplied refresh token
  GET  /v1/auth/me        — current user identity
Admin only:
  POST  /v1/auth/register      — create a user
  GET   /v1/auth/users         — list users
  PATCH /v1/auth/users/{id}    — change role / disabled
  GET   /v1/auth/audit         — recent audit log
"""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.db import audit_repository, token_repository, user_repository
from app.db.database import get_db
from app.db.user_repository import VALID_ROLES
from app.dependencies.auth import get_current_user, require_role
from app.schemas.auth import (
    AuditEntry,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
    UserUpdate,
)
from app.services import auth_service

router = APIRouter()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _issue_tokens(
    db: aiosqlite.Connection, user_id: str, role: str
) -> TokenResponse:
    access = auth_service.create_access_token(user_id, role)
    refresh, jti, expires_at = auth_service.create_refresh_token(user_id)
    await token_repository.store(db, jti, user_id, expires_at)
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    row = await user_repository.get_by_email(db, body.email)
    if not row or not auth_service.verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if row["disabled"]:
        raise HTTPException(status_code=403, detail="Account disabled")

    await audit_repository.record(db, row["id"], "login", ip=_client_ip(request))
    return await _issue_tokens(db, row["id"], row["role"])


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    db: aiosqlite.Connection = Depends(get_db),
):
    claims = auth_service.decode_token(body.refresh_token)
    if not claims or claims.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    jti = claims.get("jti")
    if not jti or not await token_repository.is_active(db, jti):
        raise HTTPException(status_code=401, detail="Refresh token revoked or expired")

    row = await user_repository.get_by_id(db, claims["sub"])
    if not row or row["disabled"]:
        raise HTTPException(status_code=401, detail="User not found or disabled")

    # Rotate: revoke the presented token before issuing a fresh pair.
    await token_repository.revoke(db, jti)
    return await _issue_tokens(db, row["id"], row["role"])


@router.post("/logout", status_code=204)
async def logout(
    body: RefreshRequest,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    claims = auth_service.decode_token(body.refresh_token)
    if claims and claims.get("jti"):
        await token_repository.revoke(db, claims["jti"])


@router.get("/me", response_model=UserOut)
async def me(user: UserOut = Depends(get_current_user)):
    return user


@router.post("/register", response_model=UserOut, status_code=201)
async def register(
    body: RegisterRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {sorted(VALID_ROLES)}")
    if await user_repository.get_by_email(db, body.email):
        raise HTTPException(status_code=409, detail="Email already registered")

    user = await user_repository.create_user(
        db,
        email=body.email,
        password_hash=auth_service.hash_password(body.password),
        role=body.role,
    )
    await audit_repository.record(
        db, admin.id, "user.create", resource=user.id, detail=user.email, ip=_client_ip(request)
    )
    return user


@router.get("/users", response_model=list[UserOut])
async def list_users(
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    return await user_repository.list_users(db)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: str,
    body: UserUpdate,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    row = await user_repository.get_by_id(db, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    if body.role is not None:
        if body.role not in VALID_ROLES:
            raise HTTPException(status_code=422, detail=f"role must be one of {sorted(VALID_ROLES)}")
        await user_repository.set_role(db, user_id, body.role)
        await audit_repository.record(
            db, admin.id, "user.role", resource=user_id, detail=body.role, ip=_client_ip(request)
        )
    if body.disabled is not None:
        await user_repository.set_disabled(db, user_id, body.disabled)
        if body.disabled:
            # Force re-login by killing outstanding refresh tokens.
            await token_repository.revoke_all_for_user(db, user_id)
        await audit_repository.record(
            db, admin.id, "user.disabled", resource=user_id,
            detail=str(body.disabled), ip=_client_ip(request),
        )

    updated = await user_repository.get_by_id(db, user_id)
    return UserOut(
        id=updated["id"], email=updated["email"], role=updated["role"],
        disabled=bool(updated["disabled"]), created_at=updated["created_at"],
    )


@router.get("/audit", response_model=list[AuditEntry])
async def audit(
    limit: int = 200,
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    return await audit_repository.list_entries(db, limit=limit)
