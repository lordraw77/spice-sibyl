"""
Regression tests for the profile-scoping (IDOR) findings of the 2026-07-17 audit.

  * 2.1 — /v1/telegram/link{,/{profile_id}}: reading, creating or deleting the
          Telegram link of a profile owned by somebody else.
  * 2.2 — DELETE /v1/knowledge/documents/{doc_id}: deleting another profile's
          document (and, with it, its chunks, graph and vectors).
  * 2.3 — GET /v1/knowledge/documents/{doc_id}/{chunks,source,wiki}: reading
          another profile's document, `source` returning its full text.
  * 3.1 — POST /v1/knowledge/documents/{doc_id}/reembed: re-ingesting another
          profile's document, which also re-attributed it to the caller.

Shape of every test: user B, holding a valid token of their own, must not be
able to touch a resource of user A even knowing its UUID.
"""

import asyncio
import uuid

import pytest

from app.db import kb_repository
from app.db.database import get_db


def _register_and_login(client, auth_headers, email: str, password: str) -> dict:
    """Create a plain user (idempotent across runs) and return its auth headers."""
    client.post(
        "/api/v1/auth/register",
        headers=auth_headers,
        json={"email": email, "password": password, "role": "user"},
    )
    tok = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    ).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


@pytest.fixture()
def other_headers(client, auth_headers):
    return _register_and_login(
        client, auth_headers, "idor-victim@example.com", "idor-password-123"
    )


def _sync(coro_factory):
    """Run a repository coroutine against a one-shot DB connection."""

    async def _inner():
        agen = get_db()
        db = await agen.__anext__()
        try:
            return await coro_factory(db)
        finally:
            await agen.aclose()

    return asyncio.run(_inner())


# ── 2.1 — Telegram link ────────────────────────────────────────
def test_telegram_link_status_of_other_profile_is_forbidden(
    client, auth_headers, other_headers
):
    victim_profile = client.post(
        "/api/v1/profiles", headers=other_headers, json={"name": "victim-tg"}
    ).json()
    resp = client.get(
        f"/api/v1/telegram/link/{victim_profile['id']}", headers=auth_headers
    )
    assert resp.status_code == 403, resp.text


def test_telegram_unlink_of_other_profile_is_forbidden(
    client, auth_headers, other_headers
):
    victim_profile = client.post(
        "/api/v1/profiles", headers=other_headers, json={"name": "victim-tg-2"}
    ).json()
    resp = client.delete(
        f"/api/v1/telegram/link/{victim_profile['id']}", headers=auth_headers
    )
    assert resp.status_code == 403, resp.text


def test_telegram_link_hijack_of_other_profile_is_forbidden(
    client, auth_headers, other_headers
):
    from app.api.v1.endpoints import telegram_link

    victim_profile = client.post(
        "/api/v1/profiles", headers=other_headers, json={"name": "victim-tg-3"}
    ).json()
    telegram_link.register_link_code("HIJACK1", 4242, "attacker")
    resp = client.post(
        "/api/v1/telegram/link",
        headers=auth_headers,
        json={"code": "HIJACK1", "profile_id": victim_profile["id"]},
    )
    assert resp.status_code == 403, resp.text
    # The one-shot code must survive the rejected attempt, not be burned.
    assert "HIJACK1" in telegram_link._link_codes


def test_telegram_link_unknown_profile_is_404(client, auth_headers):
    resp = client.get(f"/api/v1/telegram/link/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404, resp.text


def test_telegram_link_own_profile_still_works(client, auth_headers):
    own = client.post(
        "/api/v1/profiles", headers=auth_headers, json={"name": "own-tg"}
    ).json()
    resp = client.get(f"/api/v1/telegram/link/{own['id']}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["linked"] is False


# ── 2.2 — Knowledge base document delete ───────────────────────
def test_delete_document_of_other_profile_is_404(client, auth_headers, other_headers):
    victim_profile = client.post(
        "/api/v1/profiles", headers=other_headers, json={"name": "victim-kb"}
    ).json()
    doc_id = _sync(
        lambda db: kb_repository.create_document(
            db, victim_profile["id"], "secret.txt", "text/plain", 10
        )
    )

    resp = client.delete(
        f"/api/v1/knowledge/documents/{doc_id}",
        headers={**auth_headers, "X-Profile-ID": _first_profile(client, auth_headers)},
    )
    assert resp.status_code == 404, resp.text

    # …and the document is still there for its owner.
    still = _sync(lambda db: kb_repository.get_document(db, doc_id))
    assert still is not None and still.profile_id == victim_profile["id"]


def test_delete_own_document_still_works(client, auth_headers):
    pid = _first_profile(client, auth_headers)
    doc_id = _sync(
        lambda db: kb_repository.create_document(db, pid, "mine.txt", "text/plain", 10)
    )
    resp = client.delete(
        f"/api/v1/knowledge/documents/{doc_id}",
        headers={**auth_headers, "X-Profile-ID": pid},
    )
    assert resp.status_code == 204, resp.text
    gone = _sync(lambda db: kb_repository.get_document(db, doc_id))
    assert gone is None


def _first_profile(client, headers) -> str:
    profiles = client.get("/api/v1/profiles", headers=headers).json()
    assert profiles, "expected at least one profile for the caller"
    return profiles[0]["id"]


# ── 2.3 — Knowledge base document reads ────────────────────────
@pytest.fixture()
def victim_document(client, other_headers):
    """A document owned by the *other* user, for the current user to poke at."""
    profile = client.post(
        "/api/v1/profiles", headers=other_headers, json={"name": "victim-read"}
    ).json()
    doc_id = _sync(
        lambda db: kb_repository.create_document(
            db, profile["id"], "confidential.txt", "text/plain", 42
        )
    )
    return profile["id"], doc_id


@pytest.mark.parametrize("suffix", ["chunks", "source", "wiki"])
def test_reading_other_profile_document_is_404(
    client, auth_headers, victim_document, suffix
):
    _, doc_id = victim_document
    resp = client.get(
        f"/api/v1/knowledge/documents/{doc_id}/{suffix}",
        headers={**auth_headers, "X-Profile-ID": _first_profile(client, auth_headers)},
    )
    assert resp.status_code == 404, resp.text


def test_reading_own_document_still_works(client, auth_headers):
    pid = _first_profile(client, auth_headers)
    doc_id = _sync(
        lambda db: kb_repository.create_document(db, pid, "mine.txt", "text/plain", 10)
    )
    resp = client.get(
        f"/api/v1/knowledge/documents/{doc_id}/chunks",
        headers={**auth_headers, "X-Profile-ID": pid},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


# ── 3.1 — Knowledge base re-embed ──────────────────────────────
def test_reembed_other_profile_document_is_404(client, auth_headers, victim_document):
    victim_pid, doc_id = victim_document
    resp = client.post(
        f"/api/v1/knowledge/documents/{doc_id}/reembed",
        headers={**auth_headers, "X-Profile-ID": _first_profile(client, auth_headers)},
    )
    assert resp.status_code == 404, resp.text

    # The real damage of 3.1 was not the read but the silent hand-over: the
    # document must still belong to its owner.
    doc = _sync(lambda db: kb_repository.get_document(db, doc_id))
    assert doc is not None and doc.profile_id == victim_pid
