"""Pydantic schemas for the provider management endpoints."""

from pydantic import BaseModel


class ProviderStatus(BaseModel):
    id: str
    label: str
    enabled: bool
    configured: bool
    key_hint: str | None = None
    model_count: int
    capabilities: list[str]
    docs_url: str | None = None


class ProviderTestResult(BaseModel):
    provider_id: str
    ok: bool
    latency_ms: int | None = None
    model_count: int | None = None
    error: str | None = None


class ProviderUpdateRequest(BaseModel):
    enabled: bool


class ProviderKeyRequest(BaseModel):
    api_key: str
