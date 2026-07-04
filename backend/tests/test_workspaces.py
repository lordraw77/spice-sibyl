"""End-to-end tests for Phase 20.a shared workspaces & 20.b comments."""

import uuid


def _register_and_login(client, auth_headers, email: str, password: str = "member-pass-123"):
    client.post(
        "/api/v1/auth/register",
        headers=auth_headers,
        json={"email": email, "password": password, "role": "user"},
    )
    tok = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    ).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


def _make_conversation(client, headers) -> str:
    r = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"title": "T", "model": "mock/mock"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- 20.a workspaces ------------------------------------------------------


def test_create_and_list_workspace(client, auth_headers):
    r = client.post("/api/v1/workspaces", headers=auth_headers, json={"name": "Team A"})
    assert r.status_code == 201, r.text
    ws = r.json()
    assert ws["role"] == "owner" and ws["member_count"] == 1

    listed = client.get("/api/v1/workspaces", headers=auth_headers).json()
    assert any(w["id"] == ws["id"] for w in listed)


def test_add_member_and_role_gating(client, auth_headers):
    ws = client.post("/api/v1/workspaces", headers=auth_headers, json={"name": "Team B"}).json()
    member_email = f"m-{uuid.uuid4().hex[:6]}@example.com"
    member_headers = _register_and_login(client, auth_headers, member_email)

    # Non-member cannot see the workspace.
    assert all(w["id"] != ws["id"] for w in client.get("/api/v1/workspaces", headers=member_headers).json())

    # Owner adds the member as a viewer.
    add = client.post(
        f"/api/v1/workspaces/{ws['id']}/members",
        headers=auth_headers,
        json={"email": member_email, "role": "viewer"},
    )
    assert add.status_code == 201, add.text
    assert any(m["email"] == member_email for m in add.json())

    # Viewer can now see it, but cannot rename it (needs admin).
    assert any(w["id"] == ws["id"] for w in client.get("/api/v1/workspaces", headers=member_headers).json())
    forbidden = client.patch(
        f"/api/v1/workspaces/{ws['id']}", headers=member_headers, json={"name": "nope"}
    )
    assert forbidden.status_code == 403


def test_add_unknown_email_404(client, auth_headers):
    ws = client.post("/api/v1/workspaces", headers=auth_headers, json={"name": "Team C"}).json()
    r = client.post(
        f"/api/v1/workspaces/{ws['id']}/members",
        headers=auth_headers,
        json={"email": "ghost@example.com", "role": "viewer"},
    )
    assert r.status_code == 404


def test_share_conversation_requires_ownership(client, auth_headers):
    ws = client.post("/api/v1/workspaces", headers=auth_headers, json={"name": "Team D"}).json()
    other_headers = _register_and_login(client, auth_headers, f"o-{uuid.uuid4().hex[:6]}@example.com")
    other_conv = _make_conversation(client, other_headers)

    # Owner is editor+ in their own workspace but does not own the conversation.
    r = client.post(
        f"/api/v1/workspaces/{ws['id']}/conversations",
        headers=auth_headers,
        json={"conversation_id": other_conv},
    )
    assert r.status_code == 403


def test_share_conversation_visible_to_members(client, auth_headers):
    ws = client.post("/api/v1/workspaces", headers=auth_headers, json={"name": "Team E"}).json()
    member_email = f"m-{uuid.uuid4().hex[:6]}@example.com"
    member_headers = _register_and_login(client, auth_headers, member_email)
    client.post(
        f"/api/v1/workspaces/{ws['id']}/members",
        headers=auth_headers,
        json={"email": member_email, "role": "viewer"},
    )

    conv = _make_conversation(client, auth_headers)
    share = client.post(
        f"/api/v1/workspaces/{ws['id']}/conversations",
        headers=auth_headers,
        json={"conversation_id": conv},
    )
    assert share.status_code == 201, share.text

    # Member sees the shared conversation in the workspace listing.
    listed = client.get(f"/api/v1/workspaces/{ws['id']}/conversations", headers=member_headers).json()
    assert any(c["conversation_id"] == conv for c in listed)


def test_only_owner_deletes_workspace(client, auth_headers):
    ws = client.post("/api/v1/workspaces", headers=auth_headers, json={"name": "Team F"}).json()
    admin_email = f"a-{uuid.uuid4().hex[:6]}@example.com"
    admin_headers = _register_and_login(client, auth_headers, admin_email)
    client.post(
        f"/api/v1/workspaces/{ws['id']}/members",
        headers=auth_headers,
        json={"email": admin_email, "role": "admin"},
    )
    # Even an admin cannot delete the workspace.
    assert client.delete(f"/api/v1/workspaces/{ws['id']}", headers=admin_headers).status_code == 403
    assert client.delete(f"/api/v1/workspaces/{ws['id']}", headers=auth_headers).status_code == 204


def test_member_can_leave(client, auth_headers):
    ws = client.post("/api/v1/workspaces", headers=auth_headers, json={"name": "Team G"}).json()
    member_email = f"m-{uuid.uuid4().hex[:6]}@example.com"
    member_headers = _register_and_login(client, auth_headers, member_email)
    add = client.post(
        f"/api/v1/workspaces/{ws['id']}/members",
        headers=auth_headers,
        json={"email": member_email, "role": "viewer"},
    ).json()
    member_id = next(m["user_id"] for m in add if m["email"] == member_email)
    # Self-removal (leave) is allowed even for a viewer.
    assert client.delete(
        f"/api/v1/workspaces/{ws['id']}/members/{member_id}", headers=member_headers
    ).status_code == 204


# --- 20.b comments --------------------------------------------------------


def test_comment_crud_and_threading(client, auth_headers):
    conv = _make_conversation(client, auth_headers)
    top = client.post(
        f"/api/v1/conversations/{conv}/comments",
        headers=auth_headers,
        json={"body": "top-level"},
    )
    assert top.status_code == 201, top.text
    top_id = top.json()["id"]

    reply = client.post(
        f"/api/v1/conversations/{conv}/comments",
        headers=auth_headers,
        json={"body": "a reply", "parent_id": top_id},
    )
    assert reply.status_code == 201
    assert reply.json()["parent_id"] == top_id

    listed = client.get(f"/api/v1/conversations/{conv}/comments", headers=auth_headers).json()
    assert len(listed) == 2

    edited = client.patch(
        f"/api/v1/conversations/{conv}/comments/{top_id}",
        headers=auth_headers,
        json={"body": "edited"},
    )
    assert edited.status_code == 200 and edited.json()["body"] == "edited"

    # Soft delete keeps the thread anchor (row still returned, deleted flag set).
    assert client.delete(
        f"/api/v1/conversations/{conv}/comments/{top_id}", headers=auth_headers
    ).status_code == 204
    after = client.get(f"/api/v1/conversations/{conv}/comments", headers=auth_headers).json()
    deleted_row = next(c for c in after if c["id"] == top_id)
    assert deleted_row["deleted"] is True


def test_comment_access_via_workspace(client, auth_headers):
    ws = client.post("/api/v1/workspaces", headers=auth_headers, json={"name": "Team H"}).json()
    member_email = f"m-{uuid.uuid4().hex[:6]}@example.com"
    member_headers = _register_and_login(client, auth_headers, member_email)
    client.post(
        f"/api/v1/workspaces/{ws['id']}/members",
        headers=auth_headers,
        json={"email": member_email, "role": "viewer"},
    )
    conv = _make_conversation(client, auth_headers)

    # Before sharing, the member cannot see or comment on the conversation.
    assert client.get(f"/api/v1/conversations/{conv}/comments", headers=member_headers).status_code == 404

    client.post(
        f"/api/v1/workspaces/{ws['id']}/conversations",
        headers=auth_headers,
        json={"conversation_id": conv},
    )
    # After sharing, the member can comment.
    posted = client.post(
        f"/api/v1/conversations/{conv}/comments",
        headers=member_headers,
        json={"body": "hi team"},
    )
    assert posted.status_code == 201, posted.text


def test_cannot_edit_others_comment(client, auth_headers):
    ws = client.post("/api/v1/workspaces", headers=auth_headers, json={"name": "Team I"}).json()
    member_email = f"m-{uuid.uuid4().hex[:6]}@example.com"
    member_headers = _register_and_login(client, auth_headers, member_email)
    client.post(
        f"/api/v1/workspaces/{ws['id']}/members",
        headers=auth_headers,
        json={"email": member_email, "role": "viewer"},
    )
    conv = _make_conversation(client, auth_headers)
    client.post(
        f"/api/v1/workspaces/{ws['id']}/conversations",
        headers=auth_headers,
        json={"conversation_id": conv},
    )
    owner_comment = client.post(
        f"/api/v1/conversations/{conv}/comments",
        headers=auth_headers,
        json={"body": "mine"},
    ).json()
    # Member (viewer) can read but not edit the owner's comment.
    forbidden = client.patch(
        f"/api/v1/conversations/{conv}/comments/{owner_comment['id']}",
        headers=member_headers,
        json={"body": "hacked"},
    )
    assert forbidden.status_code == 403
