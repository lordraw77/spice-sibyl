"""
Shared test setup for the Phase 13 auth layer.

Env vars are set *before* any `app` import so the cached Settings singleton picks
up a throwaway SQLite file and bootstrap admin credentials.  Fixtures expose an
authenticated TestClient for the now mandatory-auth API.
"""

import os
import tempfile

# Must run before app.core.config is imported anywhere.
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_DB_FD)
os.environ.setdefault("DB_PATH", _DB_PATH)
os.environ.setdefault("ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin-password-123")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
# Use the mock provider so chat completions don't hit a real LLM in tests.
os.environ.setdefault("LITELLM_PROVIDER", "mock")
# Keep the limiter out of the way of functional tests.
os.environ.setdefault("RATE_LIMIT_DEFAULT", "10000/minute")

import asyncio  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.database import init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init_database():
    asyncio.new_event_loop().run_until_complete(init_db())
    yield
    try:
        os.unlink(_DB_PATH)
    except OSError:
        pass


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def auth_headers(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "admin-password-123"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}
