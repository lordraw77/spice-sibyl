"""Pydantic schemas for Phase 20.b annotations & comments."""

from pydantic import BaseModel, Field


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    message_id: str | None = None      # None = conversation-level comment
    parent_id: str | None = None       # None = top-level thread


class CommentUpdate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class CommentOut(BaseModel):
    id: str
    conversation_id: str
    message_id: str | None = None
    parent_id: str | None = None
    user_id: str
    author_email: str
    body: str
    deleted: bool = False
    created_at: int
    updated_at: int
