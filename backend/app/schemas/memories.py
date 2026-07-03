"""Pydantic schemas for Phase 19 per-profile persistent memory."""

from typing import Literal

from pydantic import BaseModel, Field

MemoryCategory = Literal["preference", "fact", "project", "instruction"]


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=1000)
    category: MemoryCategory = "fact"


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=1000)
    category: MemoryCategory | None = None
    enabled: bool | None = None


class MemoryOut(BaseModel):
    id: str
    profile_id: str
    content: str
    category: str
    source_conversation_id: str | None = None
    enabled: bool
    created_at: int
    updated_at: int


class MemorySettingsOut(BaseModel):
    """Per-profile memory switch (profiles.memory_enabled)."""

    memory_enabled: bool


class MemorySettingsUpdate(BaseModel):
    memory_enabled: bool
