"""End-to-end tests for the Phase 13 authentication / RBAC / audit layer."""


def test_login_success_and_me(client, auth_headers):
    me = client.get("/api/v1/auth/me", headers=auth_headers)
    assert me.status_code == 200
    assert me.json()["role"] == "admin"


def test_login_bad_password(client):
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "wrong"},
    )
    assert r.status_code == 401


def test_protected_requires_token(client):
    assert client.get("/api/v1/conversations").status_code == 401


def test_public_endpoints_open(client):
    assert client.get("/api/v1/health").status_code == 200


def test_refresh_rotation_revokes_old(client):
    tok = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "admin-password-123"},
    ).json()
    first = client.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    assert first.status_code == 200
    reuse = client.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    assert reuse.status_code == 401


def test_read_only_role_blocks_mutations(client, auth_headers):
    created = client.post(
        "/api/v1/auth/register",
        headers=auth_headers,
        json={"email": "ro@example.com", "password": "ro-password-123", "role": "read-only"},
    )
    assert created.status_code == 201, created.text
    ro = client.post(
        "/api/v1/auth/login",
        json={"email": "ro@example.com", "password": "ro-password-123"},
    ).json()
    ro_headers = {"Authorization": f"Bearer {ro['access_token']}"}
    # read-only may read
    assert client.get("/api/v1/profiles", headers=ro_headers).status_code == 200
    # but not write
    assert client.post("/api/v1/profiles", headers=ro_headers, json={"name": "x"}).status_code == 403


def test_register_requires_admin(client, auth_headers):
    user = client.post(
        "/api/v1/auth/register",
        headers=auth_headers,
        json={"email": "plain@example.com", "password": "plain-password-1", "role": "user"},
    )
    assert user.status_code == 201
    plain = client.post(
        "/api/v1/auth/login",
        json={"email": "plain@example.com", "password": "plain-password-1"},
    ).json()
    plain_headers = {"Authorization": f"Bearer {plain['access_token']}"}
    forbidden = client.post(
        "/api/v1/auth/register",
        headers=plain_headers,
        json={"email": "nope@example.com", "password": "nope-password-1", "role": "user"},
    )
    assert forbidden.status_code == 403


def test_profile_isolation_between_users(client, auth_headers):
    # Admin creates a profile; a second user must not see it.
    client.post("/api/v1/auth/register", headers=auth_headers,
                json={"email": "u2@example.com", "password": "u2-password-12", "role": "user"})
    u2 = client.post("/api/v1/auth/login",
                     json={"email": "u2@example.com", "password": "u2-password-12"}).json()
    u2_headers = {"Authorization": f"Bearer {u2['access_token']}"}

    admin_profile = client.post("/api/v1/profiles", headers=auth_headers,
                                json={"name": "admin-only"}).json()
    u2_profiles = client.get("/api/v1/profiles", headers=u2_headers).json()
    assert all(p["id"] != admin_profile["id"] for p in u2_profiles)
    # u2 cannot delete admin's profile
    forbidden = client.delete(f"/api/v1/profiles/{admin_profile['id']}", headers=u2_headers)
    assert forbidden.status_code == 403


def test_audit_log_records_login(client, auth_headers):
    entries = client.get("/api/v1/auth/audit", headers=auth_headers)
    assert entries.status_code == 200
    assert any(e["action"] == "login" for e in entries.json())
