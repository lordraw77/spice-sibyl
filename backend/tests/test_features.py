"""End-to-end tests for the admin feature-toggle system (/v1/features)."""

import uuid

from app.schemas.features import FEATURE_KEYS


def _register_and_login(client, auth_headers, role: str = "user"):
    email = f"{role}-{uuid.uuid4().hex[:6]}@example.com"
    password = "member-pass-123"
    client.post(
        "/api/v1/auth/register",
        headers=auth_headers,
        json={"email": email, "password": password, "role": role},
    )
    tok = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    ).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


def test_features_default_all_enabled(client, auth_headers):
    r = client.get("/api/v1/features", headers=auth_headers)
    assert r.status_code == 200, r.text
    flags = r.json()["features"]
    assert set(flags.keys()) == set(FEATURE_KEYS)
    assert all(flags.values())


def test_admin_can_disable_and_merge(client, auth_headers):
    put = client.put(
        "/api/v1/admin/features",
        headers=auth_headers,
        json={"flags": {"workflows": False, "workspaces": False}},
    )
    assert put.status_code == 200, put.text
    flags = client.get("/api/v1/features", headers=auth_headers).json()["features"]
    assert flags["workflows"] is False
    assert flags["workspaces"] is False
    # untouched keys keep the enabled default
    assert flags["tools"] is True
    assert flags["knowledge"] is True
    # reset for isolation from other tests
    client.put("/api/v1/admin/features", headers=auth_headers, json={"flags": {}})


def test_unknown_keys_are_dropped(client, auth_headers):
    put = client.put(
        "/api/v1/admin/features",
        headers=auth_headers,
        json={"flags": {"not_a_feature": False, "tools": False}},
    )
    assert put.status_code == 200, put.text
    flags = put.json()["features"]
    assert "not_a_feature" not in flags
    assert flags["tools"] is False
    client.put("/api/v1/admin/features", headers=auth_headers, json={"flags": {}})


def test_model_selection_defaults_unrestricted(client, auth_headers):
    r = client.get("/api/v1/admin/model-selection", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["selected"] == []
    ids = [m["id"] for m in body["models"]]
    assert ids, "catalog should not be empty in the test environment"
    # no selection stored → /models returns everything
    listed = client.get("/api/v1/models", headers=auth_headers).json()["data"]
    assert [m["id"] for m in listed] == ids


def test_model_selection_filters_models_endpoint(client, auth_headers):
    put = client.put(
        "/api/v1/admin/model-selection",
        headers=auth_headers,
        json={"models": ["no-such-model", "no-such-model", "  "]},
    )
    assert put.status_code == 200, put.text
    # dedup + blank stripping
    assert put.json()["selected"] == ["no-such-model"]
    # the allow-list hides everything not on it from /models...
    listed = client.get("/api/v1/models", headers=auth_headers).json()["data"]
    assert listed == []
    # ...while the admin surface still shows the full catalog
    full = client.get("/api/v1/admin/model-selection", headers=auth_headers).json()
    catalog_ids = [m["id"] for m in full["models"]]
    assert catalog_ids
    # selecting a real model brings it back
    real_id = catalog_ids[0]
    client.put(
        "/api/v1/admin/model-selection",
        headers=auth_headers,
        json={"models": [real_id]},
    )
    listed = client.get("/api/v1/models", headers=auth_headers).json()["data"]
    assert [m["id"] for m in listed] == [real_id]
    # reset: empty selection = unrestricted again
    client.put("/api/v1/admin/model-selection", headers=auth_headers, json={"models": []})
    listed = client.get("/api/v1/models", headers=auth_headers).json()["data"]
    assert real_id in [m["id"] for m in listed]


def test_model_selection_requires_admin(client, auth_headers):
    user = _register_and_login(client, auth_headers, role="user")
    assert client.get("/api/v1/admin/model-selection", headers=user).status_code == 403
    assert client.put(
        "/api/v1/admin/model-selection", headers=user, json={"models": []}
    ).status_code == 403


def test_runtime_config_groups_and_no_secrets(client, auth_headers):
    r = client.get("/api/v1/admin/config", headers=auth_headers)
    assert r.status_code == 200, r.text
    groups = r.json()["groups"]
    assert groups, "config snapshot should not be empty"
    entries = [e for g in groups for e in g["entries"]]
    keys = [e["key"] for e in entries]
    # every entry carries value, default, provenance flag and a description
    assert all(isinstance(e["value"], str) for e in entries)
    assert all(isinstance(e["default"], str) for e in entries)
    assert all(isinstance(e["configured"], bool) for e in entries)
    assert all(e["description"] for e in entries)
    assert all(g["label"] for g in groups)
    # an entry whose env var is set is flagged as configured (DB_PATH is set by
    # the test environment), and an untouched one falls back to its default
    by_key = {e["key"]: e for e in entries}
    assert by_key["DB_PATH"]["configured"] is True
    assert by_key["MEMORY_MAX_ITEMS"]["configured"] is False
    assert by_key["MEMORY_MAX_ITEMS"]["value"] == by_key["MEMORY_MAX_ITEMS"]["default"]
    # a few representative settings are present
    assert "DEFAULT_MODEL" in keys
    assert "APP_DEBUG" in keys
    # secrets and bootstrap-admin credentials never leak
    for key in keys:
        low = key.lower()
        assert not low.startswith("admin_")
        assert not any(m in low for m in ("api_key", "secret", "token", "password")), key
    # app_* metadata is hidden except the debug flag
    assert not any(k.startswith("APP_") and k != "APP_DEBUG" for k in keys)


def test_runtime_config_requires_admin(client, auth_headers):
    user = _register_and_login(client, auth_headers, role="user")
    assert client.get("/api/v1/admin/config", headers=user).status_code == 403


def test_non_admin_cannot_update(client, auth_headers):
    user = _register_and_login(client, auth_headers, role="user")
    # a plain user may read...
    assert client.get("/api/v1/features", headers=user).status_code == 200
    # ...but not write.
    assert client.put(
        "/api/v1/admin/features", headers=user, json={"flags": {"tools": False}}
    ).status_code == 403
