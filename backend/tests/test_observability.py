"""Phase 16 — observability & ops tests."""

import asyncio

from app.services import backup_service
from app.services.chat_service import _parse_fallback_chain, ChatService


def test_ready_endpoint(client, monkeypatch):
    # No providers configured by default in tests → not ready.
    resp = client.get("/api/v1/ready")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "checks" in body and "db" in body["checks"]
    assert body["checks"]["db"] is True


def test_metrics_endpoint_exposes_series(client):
    resp = client.get("/api/v1/metrics")
    assert resp.status_code == 200
    assert "sibyl_" in resp.text


def test_request_id_header_present(client):
    resp = client.get("/api/v1/health")
    assert resp.headers.get("X-Request-ID")


def test_parse_fallback_chain():
    from app.core.config import settings

    settings.chat_fallback_chain = "groq:llama-3.1, gemini:gemini-2.0 , bad-entry"
    parsed = _parse_fallback_chain()
    assert ("groq", "llama-3.1") in parsed
    assert ("gemini", "gemini-2.0") in parsed
    assert len(parsed) == 2  # "bad-entry" without ':' is skipped
    settings.chat_fallback_chain = ""


def test_fallback_candidates_includes_requested_model_first():
    candidates = ChatService._fallback_candidates("ollama/qwen2.5")
    assert candidates[0] == (None, "ollama/qwen2.5")


def test_backup_prune_retention(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))

    async def _run():
        for _ in range(4):
            await backup_service.make_backup()
        assert len(backup_service.list_backups()) == 4
        removed = backup_service.prune(2)
        assert removed == 2
        assert len(backup_service.list_backups()) == 2

    asyncio.new_event_loop().run_until_complete(_run())


def test_backup_export_import_roundtrip(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))

    async def _run():
        archive = await backup_service.export_profile("default")
        assert archive  # non-empty zip
        counts = await backup_service.import_profile(archive, "default")
        assert "conversations" in counts

    asyncio.new_event_loop().run_until_complete(_run())


def test_restore_rejects_path_traversal(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))

    async def _run():
        try:
            await backup_service.restore_backup("../../etc/passwd")
        except ValueError:
            return
        raise AssertionError("traversal not rejected")

    asyncio.new_event_loop().run_until_complete(_run())
