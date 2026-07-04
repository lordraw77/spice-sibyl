"""
Phase 22 i18n smoke tests.

Covers the Telegram catalog (all 5 locales, key parity, formattability) and the
per-profile UI locale persistence endpoint.
"""

import pytest

from app.telegram import i18n

LOCALES = ["it", "en", "fr", "de", "es"]

# Representative kwargs for every placeholder used across the catalog, so t()
# can be exercised for every key without raising.
_FORMAT_KWARGS = {
    "model": "gpt",
    "label": "X",
    "when": "12:00",
    "text": "hi",
    "short_id": "abc1234",
    "filename": "doc.pdf",
    "chunks": 3,
    "error": "boom",
    "sources": "a, b",
}


def test_supported_locales_are_five():
    assert set(i18n.SUPPORTED_LOCALES) == set(LOCALES)
    assert set(i18n.MESSAGES) == set(LOCALES)


def test_all_locales_have_the_same_keys():
    reference = set(i18n.MESSAGES[i18n.DEFAULT_LOCALE])
    for loc in LOCALES:
        keys = set(i18n.MESSAGES[loc])
        assert keys == reference, (
            f"{loc} key mismatch: "
            f"missing={reference - keys} extra={keys - reference}"
        )


@pytest.mark.parametrize("loc", LOCALES)
def test_every_key_formats_without_error(loc):
    for key in i18n.MESSAGES[loc]:
        result = i18n.t(loc, key, **_FORMAT_KWARGS)
        assert isinstance(result, str) and result
        # A correctly-filled template must not leave placeholder braces behind
        # for keys that declare them.
        assert "{" not in result or "}" not in result or "&lt;" in result


def test_unknown_locale_falls_back_to_default():
    # Missing locale → default (it) template, never the raw key.
    assert i18n.t("xx", "new_cleared") == i18n.MESSAGES["it"]["new_cleared"]


def test_unknown_key_returns_key_itself():
    assert i18n.t("en", "no_such_key_42") == "no_such_key_42"


# ── Profile locale persistence endpoint (Phase 22.a) ────────────────────────


def _create_profile(client, auth_headers, name="i18n-test"):
    resp = client.post("/api/v1/profiles", json={"name": name}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.parametrize("locale", LOCALES)
def test_patch_profile_locale_persists(client, auth_headers, locale):
    profile = _create_profile(client, auth_headers, f"loc-{locale}")
    resp = client.patch(
        f"/api/v1/profiles/{profile['id']}",
        json={"locale": locale},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["locale"] == locale

    # Persisted: it comes back on the list.
    listed = client.get("/api/v1/profiles", headers=auth_headers).json()
    match = next(p for p in listed if p["id"] == profile["id"])
    assert match["locale"] == locale


def test_patch_profile_rejects_unsupported_locale(client, auth_headers):
    profile = _create_profile(client, auth_headers, "loc-bad")
    resp = client.patch(
        f"/api/v1/profiles/{profile['id']}",
        json={"locale": "zz"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_patch_profile_locale_can_be_unset(client, auth_headers):
    profile = _create_profile(client, auth_headers, "loc-unset")
    client.patch(
        f"/api/v1/profiles/{profile['id']}",
        json={"locale": "fr"},
        headers=auth_headers,
    )
    resp = client.patch(
        f"/api/v1/profiles/{profile['id']}",
        json={"locale": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["locale"] is None
