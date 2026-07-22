"""
workspace_repository — persistence for Phase 20.a shared workspaces.

A workspace groups a team of users (members) and the conversations / knowledge
base documents shared into it. Access to a resource is granted either by direct
ownership (the resource's profile belongs to the caller) or by membership in a
workspace the resource is shared into.

Role hierarchy (descending privilege): owner > admin > editor > viewer.
  * owner  — created the workspace; can rename/delete it and manage every member
  * admin  — can manage members (except the owner) and share/unshare resources
  * editor — can share/unshare resources and comment
  * viewer — read-only access + comment
"""

import json
import time
import uuid

import aiosqlite

from app.schemas.workspaces import (
    MemberOut,
    SharedConversationOut,
    SharedDocumentOut,
    WorkspaceOut,
)

# Numeric rank for privilege comparisons ("at least editor" etc.).
ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
ASSIGNABLE_ROLES = frozenset({"admin", "editor", "viewer"})


def role_at_least(role: str | None, minimum: str) -> bool:
    if role is None:
        return False
    return ROLE_RANK.get(role, -1) >= ROLE_RANK.get(minimum, 99)


# --- Workspaces -----------------------------------------------------------


async def create_workspace(
    db: aiosqlite.Connection, name: str, owner_id: str
) -> WorkspaceOut:
    ws_id = str(uuid.uuid4())
    now = int(time.time())
    await db.execute(
        "INSERT INTO workspaces (id, name, owner_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (ws_id, name.strip(), owner_id, now, now),
    )
    # The owner is also a member row (role='owner') so member queries are uniform.
    await db.execute(
        "INSERT INTO workspace_members (workspace_id, user_id, role, added_at) "
        "VALUES (?, ?, 'owner', ?)",
        (ws_id, owner_id, now),
    )
    await db.commit()
    return WorkspaceOut(
        id=ws_id, name=name.strip(), owner_id=owner_id, role="owner",
        member_count=1, created_at=now, updated_at=now,
    )


async def get_workspace(
    db: aiosqlite.Connection, workspace_id: str
) -> aiosqlite.Row | None:
    async with db.execute(
        "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
    ) as cursor:
        return await cursor.fetchone()


async def list_for_user(db: aiosqlite.Connection, user_id: str) -> list[WorkspaceOut]:
    """Every workspace the user is a member of, with their role + member count."""
    async with db.execute(
        """
        SELECT w.*, m.role AS my_role,
               (SELECT COUNT(*) FROM workspace_members wm WHERE wm.workspace_id = w.id) AS member_count
        FROM workspaces w
        JOIN workspace_members m ON m.workspace_id = w.id
        WHERE m.user_id = ?
        ORDER BY w.updated_at DESC
        """,
        (user_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        WorkspaceOut(
            id=r["id"], name=r["name"], owner_id=r["owner_id"], role=r["my_role"],
            member_count=r["member_count"], created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


async def rename_workspace(
    db: aiosqlite.Connection, workspace_id: str, name: str
) -> None:
    await db.execute(
        "UPDATE workspaces SET name = ?, updated_at = ? WHERE id = ?",
        (name.strip(), int(time.time()), workspace_id),
    )
    await db.commit()


async def delete_workspace(db: aiosqlite.Connection, workspace_id: str) -> None:
    # Members / shared-resource join rows / comments cascade via FKs.
    await db.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
    await db.commit()


# --- Membership -----------------------------------------------------------


async def get_role(
    db: aiosqlite.Connection, workspace_id: str, user_id: str
) -> str | None:
    async with db.execute(
        "SELECT role FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
        (workspace_id, user_id),
    ) as cursor:
        row = await cursor.fetchone()
    return row["role"] if row else None


async def list_members(
    db: aiosqlite.Connection, workspace_id: str
) -> list[MemberOut]:
    async with db.execute(
        """
        SELECT m.user_id, m.role, m.added_at, u.email
        FROM workspace_members m
        JOIN users u ON u.id = m.user_id
        WHERE m.workspace_id = ?
        ORDER BY CASE m.role
            WHEN 'owner' THEN 0 WHEN 'admin' THEN 1
            WHEN 'editor' THEN 2 ELSE 3 END, u.email ASC
        """,
        (workspace_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        MemberOut(user_id=r["user_id"], email=r["email"], role=r["role"],
                  added_at=r["added_at"])
        for r in rows
    ]


async def add_member(
    db: aiosqlite.Connection, workspace_id: str, user_id: str, role: str
) -> None:
    now = int(time.time())
    await db.execute(
        "INSERT INTO workspace_members (workspace_id, user_id, role, added_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT (workspace_id, user_id) DO UPDATE SET role = excluded.role",
        (workspace_id, user_id, role, now),
    )
    await db.commit()


async def update_member_role(
    db: aiosqlite.Connection, workspace_id: str, user_id: str, role: str
) -> None:
    await db.execute(
        "UPDATE workspace_members SET role = ? WHERE workspace_id = ? AND user_id = ?",
        (role, workspace_id, user_id),
    )
    await db.commit()


async def remove_member(
    db: aiosqlite.Connection, workspace_id: str, user_id: str
) -> None:
    await db.execute(
        "DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
        (workspace_id, user_id),
    )
    await db.commit()


# --- Shared conversations -------------------------------------------------


async def share_conversation(
    db: aiosqlite.Connection, workspace_id: str, conversation_id: str, shared_by: str
) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO workspace_conversations "
        "(workspace_id, conversation_id, shared_by, shared_at) VALUES (?, ?, ?, ?)",
        (workspace_id, conversation_id, shared_by, int(time.time())),
    )
    await db.commit()


async def unshare_conversation(
    db: aiosqlite.Connection, workspace_id: str, conversation_id: str
) -> None:
    await db.execute(
        "DELETE FROM workspace_conversations WHERE workspace_id = ? AND conversation_id = ?",
        (workspace_id, conversation_id),
    )
    await db.commit()


async def list_shared_conversations(
    db: aiosqlite.Connection, workspace_id: str
) -> list[SharedConversationOut]:
    async with db.execute(
        """
        SELECT wc.conversation_id, wc.shared_by, wc.shared_at,
               c.title, c.model, c.updated_at
        FROM workspace_conversations wc
        JOIN conversations c ON c.id = wc.conversation_id
        WHERE wc.workspace_id = ?
        ORDER BY wc.shared_at DESC
        """,
        (workspace_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        SharedConversationOut(
            conversation_id=r["conversation_id"], title=r["title"], model=r["model"],
            shared_by=r["shared_by"], shared_at=r["shared_at"], updated_at=r["updated_at"],
        )
        for r in rows
    ]


async def workspaces_for_conversation(
    db: aiosqlite.Connection, conversation_id: str
) -> list[str]:
    async with db.execute(
        "SELECT workspace_id FROM workspace_conversations WHERE conversation_id = ?",
        (conversation_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [r["workspace_id"] for r in rows]


# --- Shared documents -----------------------------------------------------


async def share_document(
    db: aiosqlite.Connection, workspace_id: str, document_id: str, shared_by: str
) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO workspace_documents "
        "(workspace_id, document_id, shared_by, shared_at) VALUES (?, ?, ?, ?)",
        (workspace_id, document_id, shared_by, int(time.time())),
    )
    await db.commit()


async def unshare_document(
    db: aiosqlite.Connection, workspace_id: str, document_id: str
) -> None:
    await db.execute(
        "DELETE FROM workspace_documents WHERE workspace_id = ? AND document_id = ?",
        (workspace_id, document_id),
    )
    await db.commit()


async def list_shared_documents(
    db: aiosqlite.Connection, workspace_id: str
) -> list[SharedDocumentOut]:
    async with db.execute(
        """
        SELECT wd.document_id, wd.shared_by, wd.shared_at,
               d.filename, d.chunk_count, d.status
        FROM workspace_documents wd
        JOIN kb_documents d ON d.id = wd.document_id
        WHERE wd.workspace_id = ?
        ORDER BY wd.shared_at DESC
        """,
        (workspace_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        SharedDocumentOut(
            document_id=r["document_id"], filename=r["filename"],
            chunk_count=r["chunk_count"], status=r["status"],
            shared_by=r["shared_by"], shared_at=r["shared_at"],
        )
        for r in rows
    ]


# --- Shared graph workflows (Phase 36 — roadmap fase 5.2) -------------------


SHARE_ROLES = ("viewer", "editor", "approver")


async def share_workflow(
    db: aiosqlite.Connection, workspace_id: str, workflow_id: str, shared_by: str,
    role: str = "viewer",
) -> None:
    """Share (or re-share) a workflow into a workspace. ``role`` (fase 7.3) is
    what members may do with it: viewer | editor | approver — re-sharing an
    existing row updates the role in place."""
    if role not in SHARE_ROLES:
        role = "viewer"
    await db.execute(
        "INSERT INTO workspace_workflows "
        "(workspace_id, workflow_id, shared_by, shared_at, role) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(workspace_id, workflow_id) DO UPDATE SET role = excluded.role",
        (workspace_id, workflow_id, shared_by, int(time.time()), role),
    )
    await db.commit()


async def unshare_workflow(
    db: aiosqlite.Connection, workspace_id: str, workflow_id: str
) -> None:
    await db.execute(
        "DELETE FROM workspace_workflows WHERE workspace_id = ? AND workflow_id = ?",
        (workspace_id, workflow_id),
    )
    await db.commit()


async def list_shared_workflows(db: aiosqlite.Connection, workspace_id: str) -> list[dict]:
    """Graph workflows shared into the workspace, joined to their definition
    header (name/description/version). Returned as plain dicts — the schema
    (`SharedWorkflowOut`) lives with the graph-workflow schemas."""
    async with db.execute(
        """
        SELECT ww.workflow_id, ww.shared_by, ww.shared_at, ww.role,
               w.name, w.description, w.version, w.updated_at, w.graph_json
        FROM workspace_workflows ww
        JOIN workflows w ON w.id = ww.workflow_id
        WHERE ww.workspace_id = ?
        ORDER BY ww.shared_at DESC
        """,
        (workspace_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    out = []
    for r in rows:
        try:
            node_count = len((json.loads(r["graph_json"]) or {}).get("nodes") or [])
        except (ValueError, TypeError):
            node_count = 0
        out.append({
            "workflow_id": r["workflow_id"], "name": r["name"],
            "description": r["description"], "version": r["version"],
            "node_count": node_count, "shared_by": r["shared_by"],
            "shared_at": r["shared_at"], "updated_at": r["updated_at"],
            "role": r["role"] or "viewer",
        })
    return out


async def get_workflow_share_role(
    db: aiosqlite.Connection, workflow_id: str, user_id: str
) -> str | None:
    """The strongest share role the user holds on a workflow across every
    workspace they are a member of (fase 7.3): approver > editor > viewer,
    None when the workflow isn't shared with them at all."""
    async with db.execute(
        """
        SELECT ww.role FROM workspace_workflows ww
        JOIN workspace_members wm ON wm.workspace_id = ww.workspace_id
        WHERE ww.workflow_id = ? AND wm.user_id = ?
        """,
        (workflow_id, user_id),
    ) as cursor:
        rows = await cursor.fetchall()
    roles = {r["role"] or "viewer" for r in rows}
    for role in ("approver", "editor", "viewer"):
        if role in roles:
            return role
    return None


async def is_workflow_shared(
    db: aiosqlite.Connection, workspace_id: str, workflow_id: str
) -> bool:
    async with db.execute(
        "SELECT 1 FROM workspace_workflows WHERE workspace_id = ? AND workflow_id = ?",
        (workspace_id, workflow_id),
    ) as cursor:
        return (await cursor.fetchone()) is not None


# --- Cross-cutting access -------------------------------------------------


async def can_access_conversation(
    db: aiosqlite.Connection, conversation_id: str, user_id: str
) -> bool:
    """
    True when the user owns the conversation (via its profile) or is a member of
    any workspace it has been shared into. Used to gate comment read/write.
    """
    async with db.execute(
        """
        SELECT 1 FROM conversations c
        JOIN profiles p ON p.id = c.profile_id
        WHERE c.id = ? AND p.user_id = ?
        """,
        (conversation_id, user_id),
    ) as cursor:
        if await cursor.fetchone():
            return True
    async with db.execute(
        """
        SELECT 1 FROM workspace_conversations wc
        JOIN workspace_members m ON m.workspace_id = wc.workspace_id
        WHERE wc.conversation_id = ? AND m.user_id = ?
        LIMIT 1
        """,
        (conversation_id, user_id),
    ) as cursor:
        return await cursor.fetchone() is not None
