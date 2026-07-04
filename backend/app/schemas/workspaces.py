"""Pydantic schemas for Phase 20.a shared workspaces."""

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

# Role hierarchy (most → least privileged). owner > admin > editor > viewer.
WorkspaceRole = Literal["owner", "admin", "editor", "viewer"]


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WorkspaceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WorkspaceOut(BaseModel):
    id: str
    name: str
    owner_id: str
    role: str                      # the caller's role in this workspace
    member_count: int = 0
    created_at: int
    updated_at: int


class MemberOut(BaseModel):
    user_id: str
    email: str
    role: str
    added_at: int


class MemberAdd(BaseModel):
    email: EmailStr
    role: WorkspaceRole = "viewer"


class MemberUpdate(BaseModel):
    role: WorkspaceRole


class ShareConversationRequest(BaseModel):
    conversation_id: str


class ShareDocumentRequest(BaseModel):
    document_id: str


class SharedConversationOut(BaseModel):
    conversation_id: str
    title: str
    model: str
    shared_by: str
    shared_at: int
    updated_at: int


class SharedDocumentOut(BaseModel):
    document_id: str
    filename: str
    chunk_count: int
    status: str
    shared_by: str
    shared_at: int
