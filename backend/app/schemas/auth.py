"""Pydantic models for Phase 13 authentication endpoints."""

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    role: str = "user"


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: str
    role: str
    disabled: bool = False
    created_at: int


class UserUpdate(BaseModel):
    role: str | None = None
    disabled: bool | None = None


class AuditEntry(BaseModel):
    id: str
    user_id: str | None
    action: str
    resource: str | None
    detail: str | None
    ip: str | None
    created_at: int
