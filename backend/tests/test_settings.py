"""End-to-end tests for Phase 23 roaming preferences (/v1/settings)."""

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


def _make_profile(client, headers) -> str:
    r = client.post("/api/v1/profiles", headers=headers, json={"name": "P"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_user_settings_default_empty(client, auth_headers):
    r = client.get("/api/v1/settings/user", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"data": {}}


def test_user_settings_roundtrip(client, auth_headers):
    blob = {"data": {"theme": "light", "accent": "#123456", "locale": "it"}}
    put = client.put("/api/v1/settings/user", headers=auth_headers, json=blob)
    assert put.status_code == 200, put.text
    got = client.get("/api/v1/settings/user", headers=auth_headers)
    assert got.json() == blob


def test_user_settings_are_per_user(client, auth_headers):
    client.put(
        "/api/v1/settings/user",
        headers=auth_headers,
        json={"data": {"theme": "dark"}},
    )
    other = _register_and_login(client, auth_headers, f"u-{uuid.uuid4().hex[:6]}@example.com")
    assert client.get("/api/v1/settings/user", headers=other).json() == {"data": {}}


def test_profile_settings_roundtrip(client, auth_headers):
    pid = _make_profile(client, auth_headers)
    blob = {"data": {"selectedModel": "gpt-x", "temperature": 0.3}}
    put = client.put(f"/api/v1/settings/profile/{pid}", headers=auth_headers, json=blob)
    assert put.status_code == 200, put.text
    got = client.get(f"/api/v1/settings/profile/{pid}", headers=auth_headers)
    assert got.json() == blob


def test_profile_settings_unknown_404(client, auth_headers):
    assert client.get(
        f"/api/v1/settings/profile/{uuid.uuid4()}", headers=auth_headers
    ).status_code == 404


def test_profile_settings_ownership_403(client, auth_headers):
    pid = _make_profile(client, auth_headers)
    other = _register_and_login(client, auth_headers, f"o-{uuid.uuid4().hex[:6]}@example.com")
    assert client.get(
        f"/api/v1/settings/profile/{pid}", headers=other
    ).status_code == 403
    assert client.put(
        f"/api/v1/settings/profile/{pid}", headers=other, json={"data": {"x": 1}}
    ).status_code == 403


def test_read_only_blocked_on_put(client, auth_headers):
    email = f"ro-{uuid.uuid4().hex[:6]}@example.com"
    client.post(
        "/api/v1/auth/register",
        headers=auth_headers,
        json={"email": email, "password": "ro-pass-123", "role": "read-only"},
    )
    tok = client.post(
        "/api/v1/auth/login", json={"email": email, "password": "ro-pass-123"}
    ).json()
    ro = {"Authorization": f"Bearer {tok['access_token']}"}
    # GET is allowed for read-only accounts...
    assert client.get("/api/v1/settings/user", headers=ro).status_code == 200
    # ...but the mutating PUT is blocked.
    assert client.put(
        "/api/v1/settings/user", headers=ro, json={"data": {"theme": "dark"}}
    ).status_code == 403
