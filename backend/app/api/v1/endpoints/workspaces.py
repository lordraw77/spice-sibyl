"""
Phase 20.a — shared workspace endpoints.

Routes (under /v1/workspaces):
  GET    /                              — workspaces the caller belongs to
  POST   /                              — create a workspace (caller becomes owner)
  PATCH  /{ws}                          — rename (owner/admin)
  DELETE /{ws}                          — delete (owner only)

  GET    /{ws}/members                  — list members
  POST   /{ws}/members                  — add a member by email (owner/admin)
  PATCH  /{ws}/members/{uid}            — change a member's role (owner/admin)
  DELETE /{ws}/members/{uid}            — remove a member (owner/admin, or self-leave)

  GET    /{ws}/conversations            — conversations shared into the workspace
  POST   /{ws}/conversations            — share one of my conversations (editor+)
  DELETE /{ws}/conversations/{cid}      — unshare (editor+)

  GET    /{ws}/documents                — KB documents shared into the workspace
  POST   /{ws}/documents                — share one of my documents (editor+)
  DELETE /{ws}/documents/{did}          — unshare (editor+)

Access model: every route requires membership; mutations require a minimum role
(see role_at_least). Sharing a resource additionally requires the caller to own
that resource.
"""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.db import (
    audit_repository,
    kb_repository,
    profile_repository,
    user_repository,
    workspace_repository as repo,
)
from app.db.database import get_db
from app.dependencies.auth import get_current_user
from app.schemas.auth import UserOut
from app.schemas.workspaces import (
    MemberAdd,
    MemberOut,
    MemberUpdate,
    ShareConversationRequest,
    ShareDocumentRequest,
    SharedConversationOut,
    SharedDocumentOut,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceUpdate,
)

router = APIRouter()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _require_membership(
    db: aiosqlite.Connection, workspace_id: str, user: UserOut, minimum: str = "viewer"
) -> str:
    """Return the caller's role in the workspace, enforcing a minimum privilege."""
    if not await repo.get_workspace(db, workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    role = await repo.get_role(db, workspace_id, user.id)
    if role is None:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    if not repo.role_at_least(role, minimum):
        raise HTTPException(status_code=403, detail="Insufficient workspace role")
    return role


async def _assert_owns_conversation(
    db: aiosqlite.Connection, conversation_id: str, user: UserOut
) -> None:
    async with db.execute(
        "SELECT profile_id FROM conversations WHERE id = ?", (conversation_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")
    profile = await profile_repository.get_profile(db, row["profile_id"])
    if not profile or profile.user_id != user.id:
        raise HTTPException(status_code=403, detail="Conversation does not belong to you")


async def _assert_owns_document(
    db: aiosqlite.Connection, document_id: str, user: UserOut
) -> None:
    doc = await kb_repository.get_document(db, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    profile = await profile_repository.get_profile(db, doc.profile_id)
    if not profile or profile.user_id != user.id:
        raise HTTPException(status_code=403, detail="Document does not belong to you")


# --- Workspaces -----------------------------------------------------------


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    return await repo.list_for_user(db, user.id)


@router.post("", response_model=WorkspaceOut, status_code=201)
async def create_workspace(
    body: WorkspaceCreate,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    ws = await repo.create_workspace(db, body.name, user.id)
    await audit_repository.record(
        db, user.id, "workspace.create", resource=ws.id, ip=_client_ip(request)
    )
    return ws


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
async def rename_workspace(
    workspace_id: str,
    body: WorkspaceUpdate,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _require_membership(db, workspace_id, user, minimum="admin")
    await repo.rename_workspace(db, workspace_id, body.name)
    for ws in await repo.list_for_user(db, user.id):
        if ws.id == workspace_id:
            return ws
    raise HTTPException(status_code=404, detail="Workspace not found")


@router.delete("/{workspace_id}", status_code=204)
async def delete_workspace(
    workspace_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _require_membership(db, workspace_id, user, minimum="owner")
    await repo.delete_workspace(db, workspace_id)
    await audit_repository.record(
        db, user.id, "workspace.delete", resource=workspace_id, ip=_client_ip(request)
    )


# --- Members --------------------------------------------------------------


@router.get("/{workspace_id}/members", response_model=list[MemberOut])
async def list_members(
    workspace_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _require_membership(db, workspace_id, user)
    return await repo.list_members(db, workspace_id)


@router.post("/{workspace_id}/members", response_model=list[MemberOut], status_code=201)
async def add_member(
    workspace_id: str,
    body: MemberAdd,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _require_membership(db, workspace_id, user, minimum="admin")
    target = await user_repository.get_by_email(db, body.email)
    if not target:
        raise HTTPException(status_code=404, detail="No account with that email")
    if body.role not in repo.ASSIGNABLE_ROLES:
        raise HTTPException(status_code=422, detail="Invalid role")
    # Never let a non-owner overwrite the owner's row via re-invite.
    existing = await repo.get_role(db, workspace_id, target["id"])
    if existing == "owner":
        raise HTTPException(status_code=409, detail="User is the workspace owner")
    await repo.add_member(db, workspace_id, target["id"], body.role)
    return await repo.list_members(db, workspace_id)


@router.patch("/{workspace_id}/members/{member_id}", response_model=list[MemberOut])
async def update_member(
    workspace_id: str,
    member_id: str,
    body: MemberUpdate,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _require_membership(db, workspace_id, user, minimum="admin")
    if body.role not in repo.ASSIGNABLE_ROLES:
        raise HTTPException(status_code=422, detail="Invalid role")
    target_role = await repo.get_role(db, workspace_id, member_id)
    if target_role is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if target_role == "owner":
        raise HTTPException(status_code=409, detail="Cannot change the owner's role")
    await repo.update_member_role(db, workspace_id, member_id, body.role)
    return await repo.list_members(db, workspace_id)


@router.delete("/{workspace_id}/members/{member_id}", status_code=204)
async def remove_member(
    workspace_id: str,
    member_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    my_role = await _require_membership(db, workspace_id, user)
    target_role = await repo.get_role(db, workspace_id, member_id)
    if target_role is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if target_role == "owner":
        raise HTTPException(status_code=409, detail="Cannot remove the workspace owner")
    # Members may remove themselves (leave); otherwise admin+ is required.
    if member_id != user.id and not repo.role_at_least(my_role, "admin"):
        raise HTTPException(status_code=403, detail="Insufficient workspace role")
    await repo.remove_member(db, workspace_id, member_id)


# --- Shared conversations -------------------------------------------------


@router.get("/{workspace_id}/conversations", response_model=list[SharedConversationOut])
async def list_shared_conversations(
    workspace_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _require_membership(db, workspace_id, user)
    return await repo.list_shared_conversations(db, workspace_id)


@router.post("/{workspace_id}/conversations", response_model=list[SharedConversationOut], status_code=201)
async def share_conversation(
    workspace_id: str,
    body: ShareConversationRequest,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _require_membership(db, workspace_id, user, minimum="editor")
    await _assert_owns_conversation(db, body.conversation_id, user)
    await repo.share_conversation(db, workspace_id, body.conversation_id, user.id)
    return await repo.list_shared_conversations(db, workspace_id)


@router.delete("/{workspace_id}/conversations/{conversation_id}", status_code=204)
async def unshare_conversation(
    workspace_id: str,
    conversation_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _require_membership(db, workspace_id, user, minimum="editor")
    await repo.unshare_conversation(db, workspace_id, conversation_id)


# --- Shared documents -----------------------------------------------------


@router.get("/{workspace_id}/documents", response_model=list[SharedDocumentOut])
async def list_shared_documents(
    workspace_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _require_membership(db, workspace_id, user)
    return await repo.list_shared_documents(db, workspace_id)


@router.post("/{workspace_id}/documents", response_model=list[SharedDocumentOut], status_code=201)
async def share_document(
    workspace_id: str,
    body: ShareDocumentRequest,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _require_membership(db, workspace_id, user, minimum="editor")
    await _assert_owns_document(db, body.document_id, user)
    await repo.share_document(db, workspace_id, body.document_id, user.id)
    return await repo.list_shared_documents(db, workspace_id)


@router.delete("/{workspace_id}/documents/{document_id}", status_code=204)
async def unshare_document(
    workspace_id: str,
    document_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    user: UserOut = Depends(get_current_user),
):
    await _require_membership(db, workspace_id, user, minimum="editor")
    await repo.unshare_document(db, workspace_id, document_id)
