# Workspaces and collaboration

Team features built on the Phase 13 accounts and Phase 17 knowledge base scoping: shared workspaces with role-based access, and threaded comments on shared conversations.

## Shared workspaces

**What it does.** A workspace is a team container owned by a user. Other accounts join as **members** with a role, and the owner shares individual conversations and knowledge base documents *into* the workspace, making them visible to every member. Resources keep their original owner — sharing is a join relationship (`workspace_conversations` / `workspace_documents`), not a copy — so unsharing simply removes the link.

**Roles.** Four levels, in descending order of privilege:

| Role | Can do |
|------|--------|
| **owner** | Everything, plus rename/delete the workspace and manage every member. Created the workspace; exactly one per workspace. |
| **admin** | Manage members (add/change role/remove, except the owner) and share/unshare resources. |
| **editor** | Share/unshare their own resources and comment. |
| **viewer** | Read shared resources and comment. |

Any member (including a viewer) can **leave** a workspace themselves; only admin+ can remove *other* members. Sharing a conversation or document requires editor+ **and** ownership of that resource — you cannot share something that is not yours.

**How to use it.** Open the **Workspace** page from the navbar:

- The left sidebar lists the workspaces you belong to (with your role and member count) and a field to create a new one — creating it makes you the owner.
- Selecting a workspace opens the detail pane with three cards: **Members**, **Shared conversations** and **Shared documents**.
- **Members** — invite by email (the account must already exist), change a member's role inline, or remove them. Management controls appear only for admin+; the owner row is not editable.
- **Shared conversations / documents** — pick one of your own conversations or KB documents from the dropdown and share it; every member then sees it in the list. The **✕** unshares it (editor+).

![Workspace management](screenshots/workspace.png)

**API.**

| Method & path | Purpose | Minimum role |
|---------------|---------|--------------|
| `GET /v1/workspaces` | Workspaces the caller belongs to | member |
| `POST /v1/workspaces` | Create (caller becomes owner) | — |
| `PATCH /v1/workspaces/{ws}` | Rename | admin |
| `DELETE /v1/workspaces/{ws}` | Delete | owner |
| `GET/POST /v1/workspaces/{ws}/members` | List / invite by email | view / admin |
| `PATCH/DELETE /v1/workspaces/{ws}/members/{uid}` | Change role / remove (or self-leave) | admin |
| `GET/POST /v1/workspaces/{ws}/conversations` | List / share a conversation | view / editor |
| `DELETE /v1/workspaces/{ws}/conversations/{cid}` | Unshare a conversation | editor |
| `GET/POST /v1/workspaces/{ws}/documents` | List / share a KB document | view / editor |
| `DELETE /v1/workspaces/{ws}/documents/{did}` | Unshare a KB document | editor |

## Annotations & comments

**What it does.** Threaded comments on a shared conversation. A comment can be a top-level thread or a reply (`parent_id`), and can optionally be anchored to a specific message (`message_id`). Comments are **soft-deleted** — a removed comment is blanked and flagged rather than dropped, so replies underneath it keep their place in the thread.

**Who can see them.** Access mirrors the conversation's reach: its owner, or any member of a workspace it has been shared into, can read and post. Editing and deleting are restricted to the comment's **author** — no one else can alter your text, regardless of workspace role.

**How to use it.** In the Workspace page, each shared conversation has a **Commenti / Comments** toggle that opens a threaded panel beneath it. Write a top-level comment in the box, use **Rispondi / Reply** to nest a response, and **Modifica / Elimina** (edit / delete) on your own comments. Threads nest visually by indentation.

![Threaded comments on a shared conversation](screenshots/workspace-commenti.png)

**API** (under `/v1/conversations/{id}/comments`):

| Method & path | Purpose |
|---------------|---------|
| `GET /` | List every comment on the conversation (threaded client-side by `parent_id`) |
| `POST /` | Add a comment (`body`, optional `message_id`, optional `parent_id`) |
| `PATCH /{comment_id}` | Edit your comment |
| `DELETE /{comment_id}` | Soft-delete your comment |

A caller with no relationship to the conversation gets a `404` (rather than `403`) so the existence of private conversations is never leaked.

## Data model

- `workspaces` — `id`, `name`, `owner_id`, timestamps.
- `workspace_members` — `(workspace_id, user_id)` with `role`; the owner is stored as a member row (`role='owner'`) so membership queries are uniform.
- `workspace_conversations` / `workspace_documents` — join tables linking a workspace to shared conversations / KB documents, with `shared_by` and `shared_at`.
- `comments` — `id`, `conversation_id`, nullable `message_id`, nullable `parent_id`, `user_id`, `body`, `deleted`, timestamps.

All tables cascade on delete via foreign keys, so removing a workspace, conversation or user cleans up the dependent rows automatically.

> Real-time collaboration (multiple users live in one conversation over WebSocket, with presence indicators) is planned as Phase 20.c and is not yet implemented.
