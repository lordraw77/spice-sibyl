"""
Regression tests for the 2026-07-17 audit findings closed on 2026-08-25.

  * 1.3 — SSRF through a redirect: `assert_public_url` vetted the first URL,
          then every client ran with follow_redirects=True, so a public host
          could bounce the request to 169.254.169.254 with the caller's headers.
  * 2.5 — no rate limit on /auth/login and /auth/refresh: the limiter depended
          on get_current_user, so by construction it could not cover the routes
          that have no authenticated user yet.
  * 2.6 — user enumeration by timing: `not row or not verify_password(...)`
          short-circuited, so an unknown email skipped bcrypt entirely.
  * 2.8 — failed logins were absent from the audit log.
"""

import asyncio
import socket

import httpx
import pytest

from app.core import safe_http
from app.dependencies import rate_limit as rl
from app.services.rate_limiting import _memory_limiter

# Hostnames resolved without touching DNS, so the guard's real logic is what is
# under test rather than the test environment's name resolution.
_FAKE_DNS = {
    "public.test": "93.184.216.34",
    "evil.test": "93.184.216.35",
    "metadata.test": "169.254.169.254",
    "internal.test": "10.0.0.5",
}


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch):
    def getaddrinfo(host, port, *args, **kwargs):
        ip = _FAKE_DNS.get(host)
        if ip is None:
            raise OSError(f"unknown test host {host!r}")
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]

    monkeypatch.setattr(safe_http.socket, "getaddrinfo", getaddrinfo)


def _client(handler) -> httpx.AsyncClient:
    # follow_redirects=False is the whole point: httpx must hand each hop back
    # so the guard can vet it.
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )


# ── 1.3 — SSRF via redirect ────────────────────────────────────
def test_plain_request_to_a_public_host_succeeds():
    def handler(request):
        return httpx.Response(200, text="ok")

    async def go():
        async with _client(handler) as c:
            resp = await safe_http.safe_request(c, "GET", "https://public.test/x")
            return resp.text

    assert asyncio.run(go()) == "ok"


@pytest.mark.parametrize("target", [
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata by IP
    "http://metadata.test/latest/meta-data/",     # …and by name
    "http://internal.test/admin",                 # private range
])
def test_redirect_into_the_internal_network_is_blocked(target):
    """The exact attack: a vetted public host answers 302 towards the inside."""

    def handler(request):
        if request.url.host == "public.test":
            return httpx.Response(302, headers={"location": target})
        raise AssertionError(f"guard let the request through to {request.url}")

    async def go():
        async with _client(handler) as c:
            await safe_http.safe_request(c, "GET", "https://public.test/x")

    with pytest.raises(safe_http.BlockedURLError):
        asyncio.run(go())


def test_redirect_to_another_public_host_is_followed():
    """The guard must not break legitimate redirects (http→https, CDNs, …)."""

    def handler(request):
        if request.url.host == "public.test":
            return httpx.Response(302, headers={"location": "https://evil.test/ok"})
        return httpx.Response(200, text="landed")

    async def go():
        async with _client(handler) as c:
            resp = await safe_http.safe_request(c, "GET", "https://public.test/x")
            return resp.text

    assert asyncio.run(go()) == "landed"


def test_redirect_loop_stops_at_the_hop_cap():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://public.test/again"})

    async def go():
        async with _client(handler) as c:
            await safe_http.safe_request(c, "GET", "https://public.test/x",
                                         max_redirects=3)

    with pytest.raises(safe_http.BlockedURLError, match="too many redirects"):
        asyncio.run(go())


def test_initial_url_is_still_vetted():
    def handler(request):
        raise AssertionError("guard should have refused before sending")

    async def go():
        async with _client(handler) as c:
            await safe_http.safe_request(c, "GET", "http://internal.test/x")

    with pytest.raises(safe_http.BlockedURLError):
        asyncio.run(go())


# ── 2.5 — login rate limit and progressive lockout ─────────────
@pytest.fixture()
def tight_auth_limit(monkeypatch):
    """Put the production-tight limit back for the tests that are about it."""
    monkeypatch.setattr(rl, "_AUTH_MAX", 3)
    monkeypatch.setattr(rl, "_AUTH_WINDOW", 60)
    _memory_limiter.hits.clear()
    yield
    _memory_limiter.hits.clear()


def test_login_is_rate_limited_by_ip(client, tight_auth_limit):
    payload = {"email": "nobody@example.com", "password": "wrong"}
    codes = [client.post("/api/v1/auth/login", json=payload).status_code
             for _ in range(5)]
    assert 429 in codes, codes
    # The limit must bite before the attempts are exhausted, not after.
    assert codes[:3] == [401, 401, 401], codes


def test_refresh_is_rate_limited_too(client, tight_auth_limit):
    payload = {"refresh_token": "not-a-token"}
    codes = [client.post("/api/v1/auth/refresh", json=payload).status_code
             for _ in range(5)]
    assert 429 in codes, codes


def test_rate_limited_response_carries_retry_after(client, tight_auth_limit):
    payload = {"email": "nobody@example.com", "password": "wrong"}
    for _ in range(5):
        resp = client.post("/api/v1/auth/login", json=payload)
        if resp.status_code == 429:
            assert int(resp.headers["retry-after"]) >= 1
            return
    raise AssertionError("never got throttled")


def test_lockout_tier_triggers_on_repeated_failures(client, monkeypatch):
    """Enough failures for one email lock it out even under a loose IP limit."""
    monkeypatch.setattr(rl, "_LOCKOUT_TIERS", ((60.0, 3),))
    _memory_limiter.hits.clear()

    payload = {"email": "victim@example.com", "password": "wrong"}
    codes = [client.post("/api/v1/auth/login", json=payload).status_code
             for _ in range(5)]
    assert codes[:3] == [401, 401, 401], codes
    assert codes[3] == 429, codes

    # The lockout is per email: a different one is unaffected.
    other = client.post("/api/v1/auth/login",
                        json={"email": "bystander@example.com", "password": "wrong"})
    assert other.status_code == 401, other.text


def test_correct_login_still_works_under_the_guard(client):
    resp = client.post("/api/v1/auth/login",
                       json={"email": "admin@example.com",
                             "password": "admin-password-123"})
    assert resp.status_code == 200, resp.text


# ── 2.6 — user enumeration by timing ───────────────────────────
def test_unknown_email_still_runs_bcrypt(client, monkeypatch):
    """The fix is behavioural, not statistical: assert bcrypt actually runs.

    Timing the two paths would be a flaky way to test this; what the finding is
    really about is the short-circuit, so we check the expensive call happens on
    both branches.
    """
    from app.api.v1.endpoints import auth as auth_endpoint

    calls: list[str] = []
    real = auth_endpoint.auth_service.verify_password

    def spy(plaintext, hashed):
        calls.append(hashed)
        return real(plaintext, hashed)

    monkeypatch.setattr(auth_endpoint.auth_service, "verify_password", spy)

    client.post("/api/v1/auth/login",
                json={"email": "definitely-not-a-user@example.com", "password": "x"})
    assert len(calls) == 1, "bcrypt was skipped for an unknown email"
    assert calls[0] == auth_endpoint._DUMMY_HASH

    calls.clear()
    client.post("/api/v1/auth/login",
                json={"email": "admin@example.com", "password": "wrong"})
    assert len(calls) == 1
    assert calls[0] != auth_endpoint._DUMMY_HASH


# ── 2.8 — failed logins in the audit log ───────────────────────
def test_failed_login_is_audited(client, auth_headers):
    client.post("/api/v1/auth/login",
                json={"email": "admin@example.com", "password": "definitely-wrong"})
    entries = client.get("/api/v1/auth/audit", headers=auth_headers).json()
    assert any(e["action"] == "login_failed" for e in entries), entries
