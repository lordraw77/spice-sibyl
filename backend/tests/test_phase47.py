"""
Phase 47 — roadmap fase 15: connectors and multimodal nodes.

Covers the curated connector library (15.1 — request mapping + execution over
http.request), the ssh.exec node validation/allow-list (15.2), the browser node
guardrails (15.3), the rss.read poll trigger with guid dedup and first-poll
seeding (15.4), and the doc.convert markitdown node (15.5). Network/binary-
dependent paths (real SSH/Playwright) are exercised at the unit boundary
(request building, validation) so the suite needs no external services.
"""

import asyncio

import pytest

from app.core.config import settings
from app.data import node_catalog
from app.services import workflow_graph_service as engine


# ── 15.1 curated connector library ──────────────────────────────────────────

def test_connector_slack_request_maps_to_http():
    spec = engine._connector_request(
        "slack.postMessage",
        {"token": "xoxb-1", "channel": "#general", "text": "hi", "thread_ts": "123.45"},
    )
    assert spec["method"] == "POST"
    assert spec["url"] == "https://slack.com/api/chat.postMessage"
    assert spec["headers"]["Authorization"] == "Bearer xoxb-1"
    assert spec["body"] == {"channel": "#general", "text": "hi", "thread_ts": "123.45"}


def test_connector_github_and_jira_requests():
    gh = engine._connector_request(
        "github.createIssue",
        {"token": "ghp", "repo": "acme/app", "title": "Bug", "body": "boom", "labels": ["bug"]},
    )
    assert gh["url"] == "https://api.github.com/repos/acme/app/issues"
    assert gh["body"]["labels"] == ["bug"]

    jira = engine._connector_request(
        "jira.createIssue",
        {"base_url": "https://x.atlassian.net/", "email": "a@b.c", "token": "t",
         "project_key": "OPS", "summary": "S", "issue_type": "Bug"},
    )
    assert jira["url"] == "https://x.atlassian.net/rest/api/3/issue"
    assert jira["headers"]["Authorization"].startswith("Basic ")
    assert jira["body"]["fields"]["project"]["key"] == "OPS"
    assert jira["body"]["fields"]["issuetype"]["name"] == "Bug"


def test_connector_sheets_read_carries_passthrough_knobs():
    spec = engine._connector_request(
        "sheets.read",
        {"token": "t", "spreadsheet_id": "sid", "range": "Sheet1!A1:B2",
         "timeout": 5, "maxRequestsPerMinute": 30},
    )
    assert spec["method"] == "GET"
    assert spec["url"].endswith("/values/Sheet1!A1:B2")
    assert spec["timeout"] == 5
    assert spec["maxRequestsPerMinute"] == 30


def test_connector_unknown_operation_raises():
    with pytest.raises(ValueError, match="unknown operation"):
        engine._connector_request("nope.doThing", {})


def test_exec_connector_runs_via_http_request(monkeypatch):
    captured = {}

    async def _fake_http(spec):
        captured["spec"] = spec
        return {"status": 200, "ok": True, "json": {"ok": True}, "text": ""}

    monkeypatch.setattr(engine, "_exec_http_request", _fake_http)
    out = asyncio.run(engine._exec_connector(
        "discord.postMessage",
        {"webhook_url": "https://discord.com/api/webhooks/x", "text": "yo"}, None,
    ))
    assert out["operation"] == "discord.postMessage"
    assert out["ok"] is True
    assert captured["spec"]["body"] == {"content": "yo"}


# ── 15.2 ssh.exec ───────────────────────────────────────────────────────────

def test_ssh_exec_requires_host_and_command():
    with pytest.raises(ValueError, match="'host' is required"):
        asyncio.run(engine._exec_ssh_exec({}))
    with pytest.raises(ValueError, match="'command' is required"):
        asyncio.run(engine._exec_ssh_exec({"host": "h"}))


def test_ssh_exec_host_allow_list(monkeypatch):
    monkeypatch.setattr(settings, "graph_workflow_ssh_allowed_hosts", "trusted.example.com")
    with pytest.raises(ValueError, match="not in GRAPH_WORKFLOW_SSH_ALLOWED_HOSTS"):
        asyncio.run(engine._exec_ssh_exec({"host": "evil.example.com", "command": "ls"}))
    assert engine._ssh_host_allowed("trusted.example.com")
    assert not engine._ssh_host_allowed("evil.example.com")
    # An empty allow-list permits any host (validation passes to the paramiko step).
    monkeypatch.setattr(settings, "graph_workflow_ssh_allowed_hosts", "")
    assert engine._ssh_host_allowed("anything")


# ── 15.3 browser ────────────────────────────────────────────────────────────

def test_browser_requires_http_url():
    with pytest.raises(ValueError, match="must be an http"):
        asyncio.run(engine._exec_browser({"url": "ftp://x"}))


# ── 15.4 rss.read trigger ───────────────────────────────────────────────────

_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Feed</title>
  <item><guid>g1</guid><title>First</title><link>http://x/1</link>
        <pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate><description>one</description></item>
  <item><guid>g2</guid><title>Second</title><link>http://x/2</link><description>two</description></item>
</channel></rss>"""

_ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>a1</id><title>Atom one</title><link href="http://y/1"/>
         <updated>2026-01-01T00:00:00Z</updated><summary>s1</summary></entry>
</feed>"""


def test_parse_feed_entries_rss_and_atom():
    rss = engine._parse_feed_entries(_RSS)
    assert [e["guid"] for e in rss] == ["g1", "g2"]
    assert rss[0]["title"] == "First"
    assert rss[0]["link"] == "http://x/1"
    assert rss[0]["summary"] == "one"

    atom = engine._parse_feed_entries(_ATOM)
    assert atom[0]["guid"] == "a1"
    assert atom[0]["link"] == "http://y/1"
    assert atom[0]["summary"] == "s1"


def test_parse_feed_entries_malformed_is_empty():
    assert engine._parse_feed_entries("<not xml") == []


class _FakeResp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, text):
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        return _FakeResp(self._text)


def _patch_feed(monkeypatch, text, fired, configs):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(text))

    async def _fake_run(db, wf_id, profile_id, *, trigger_type, trigger_payload, **kw):
        fired.append(trigger_payload)

    async def _fake_update(db, tr_id, cfg):
        configs.append(cfg)

    monkeypatch.setattr(engine, "run_workflow", _fake_run)
    monkeypatch.setattr(engine.repo, "update_trigger_config", _fake_update)


def test_rss_read_first_poll_seeds_without_firing(monkeypatch):
    fired, configs = [], []
    _patch_feed(monkeypatch, _RSS, fired, configs)
    row = {"id": "t1", "workflow_id": "w", "wf_profile_id": "default"}
    seeded = asyncio.run(engine._poll_rss_read(None, row, {"url": "http://x/feed"}))
    assert seeded is True
    assert fired == []  # first poll only seeds
    assert set(configs[-1]["state"]) == {"g1", "g2"}


def test_rss_read_fires_only_new_entries(monkeypatch):
    fired, configs = [], []
    _patch_feed(monkeypatch, _RSS, fired, configs)
    row = {"id": "t1", "workflow_id": "w", "wf_profile_id": "default"}
    # g1 already seen → only g2 fires; g2 lands in the seen-set.
    result = asyncio.run(engine._poll_rss_read(None, row, {"url": "http://x/feed", "state": ["g1"]}))
    assert result is True
    assert [e["guid"] for e in fired] == ["g2"]
    assert "g2" in configs[-1]["state"]

    # A second identical poll fires nothing (both guids now seen).
    fired.clear(); configs.clear()
    already = ["g1", "g2"]
    result = asyncio.run(engine._poll_rss_read(None, row, {"url": "http://x/feed", "state": already}))
    assert fired == []


def test_rss_read_requires_feed_url():
    row = {"id": "t1", "workflow_id": "w", "wf_profile_id": "default"}
    with pytest.raises(ValueError, match="feed URL"):
        asyncio.run(engine._poll_rss_read(None, row, {"url": "not-a-url"}))


# ── 15.5 doc.convert ────────────────────────────────────────────────────────

def test_doc_convert_html_to_markdown(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "graph_workflow_files_dir", str(tmp_path))
    (tmp_path / "note.html").write_text("<html><body><h1>Title</h1><p>Hello world</p></body></html>")
    out = asyncio.run(engine._exec_doc_convert({"path": "note.html"}, None))
    assert out["path"] == "note.html"
    assert "Hello world" in out["markdown"]
    assert out["chars"] == len(out["markdown"])


def test_doc_convert_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "graph_workflow_files_dir", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        asyncio.run(engine._exec_doc_convert({"path": "nope.pdf"}, None))


def test_doc_convert_path_from_node_input(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "graph_workflow_files_dir", str(tmp_path))
    (tmp_path / "in.html").write_text("<html><body>from input</body></html>")
    out = asyncio.run(engine._exec_doc_convert({}, "in.html"))
    assert "from input" in out["markdown"]


# ── catalog + trigger registration ──────────────────────────────────────────

def test_new_node_types_in_catalog():
    catalog = asyncio.run(node_catalog.node_catalog())
    types = {n.type for n in catalog}
    for expected in (
        "connector.slack.postMessage", "connector.github.createIssue",
        "connector.sheets.append", "ssh.exec", "browser", "doc.convert", "rss.read",
    ):
        assert expected in types, expected
    connectors = [n for n in catalog if n.category == "connector"]
    assert len(connectors) >= 5


def test_rss_read_trigger_requires_url(client, auth_headers):
    graph = {
        "nodes": [
            {"id": "t", "type": "rss.read"},
            {"id": "out", "type": "set", "params": {"fields": {"ok": True}}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "out"}],
    }
    wf = client.post(
        "/api/v1/graph-workflows", json={"name": "rss wf", "graph": graph}, headers=auth_headers,
    ).json()

    bad = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "rss.read", "config": {}}, headers=auth_headers,
    )
    assert bad.status_code == 400

    ok = client.post(
        f"/api/v1/graph-workflows/{wf['id']}/triggers",
        json={"type": "rss.read", "config": {"url": "https://example.com/feed.xml"}},
        headers=auth_headers,
    )
    assert ok.status_code in (200, 201), ok.text
