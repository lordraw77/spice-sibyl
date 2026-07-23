"""
Phase 45 — roadmap fase 13: copilot and workflow-as-code.

Covers "explain / repair" for a failed run (13.2 — the failed node's catalog
entry, input and error go to the LLM, which returns a plain explanation and an
optional corrected params object, never applied automatically) and Git sync of
workflow versions (13.3 — every saved version committed as JSON to a
configured repo, pull importing changes back as new draft versions). Fase
13.1 (expression autocomplete) is a frontend-only feature with no backend
surface to test here.
"""

import asyncio
import json
import subprocess

import pytest

from app.db import graph_workflow_repository as repo
from app.services import workflow_graph_service as engine

_HTTP_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual"},
        {"id": "req", "type": "http.request", "params": {"url": "not-a-url", "method": "GET"}},
    ],
    "edges": [{"id": "e1", "source": "t", "target": "req"}],
}


def _make(client, auth_headers, graph=None, name="phase45 wf", **extra):
    body = {"name": name, "graph": graph or _HTTP_GRAPH, **extra}
    resp = client.post("/api/v1/graph-workflows", json=body, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_failed_run(wf, *, node_id="req", node_type="http.request", node_input=None, error="boom"):
    async def _seed():
        db = await engine._connect()
        try:
            graph_json = json.dumps(_HTTP_GRAPH)
            run_id = await repo.create_run(db, wf["id"], wf["profile_id"], "manual", graph_json)
            nr_id = await repo.start_node_run(db, run_id, node_id, node_type, node_input or {})
            await repo.finish_node_run(db, nr_id, "error", error=error)
            await repo.set_run_status(db, run_id, "failed", error=error)
            return run_id
        finally:
            await db.close()

    return asyncio.run(_seed())


def _fake_llm(monkeypatch, content: str):
    async def _fake(request):
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }, "miss"

    monkeypatch.setattr("app.workflow.nodes.llm._cached_complete", _fake)


# ── fase 13.2: explain / repair ──────────────────────────────────────────────

def test_explain_run_returns_explanation_and_patch(client, auth_headers, monkeypatch):
    wf = _make(client, auth_headers)
    run_id = _seed_failed_run(wf, node_input={"url": "not-a-url"}, error="invalid URL")
    _fake_llm(
        monkeypatch,
        json.dumps({
            "explanation": "The URL is missing its scheme.",
            "proposed_params": {"url": "https://example.com", "method": "GET"},
        }),
    )

    resp = client.post(f"/api/v1/graph-workflows/runs/{run_id}/explain", json={}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["node_id"] == "req"
    assert "scheme" in data["explanation"]
    assert data["proposed_params"]["url"] == "https://example.com"
    ops = {op["path"]: op for op in data["patch"]}
    assert ops["/url"]["op"] == "replace"
    assert ops["/url"]["value"] == "https://example.com"


def test_explain_run_without_proposed_params_has_no_patch(client, auth_headers, monkeypatch):
    wf = _make(client, auth_headers)
    run_id = _seed_failed_run(wf)
    _fake_llm(monkeypatch, json.dumps({"explanation": "Network unreachable.", "proposed_params": None}))

    resp = client.post(f"/api/v1/graph-workflows/runs/{run_id}/explain", json={}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["proposed_params"] is None
    assert data["patch"] is None


def test_explain_run_404_when_no_failed_node(client, auth_headers):
    wf = _make(client, auth_headers)

    async def _seed_ok_run():
        db = await engine._connect()
        try:
            run_id = await repo.create_run(db, wf["id"], wf["profile_id"], "manual", json.dumps(_HTTP_GRAPH))
            await repo.set_run_status(db, run_id, "completed")
            return run_id
        finally:
            await db.close()

    run_id = asyncio.run(_seed_ok_run())
    resp = client.post(f"/api/v1/graph-workflows/runs/{run_id}/explain", json={}, headers=auth_headers)
    assert resp.status_code == 409


def test_explain_run_not_found(client, auth_headers):
    resp = client.post("/api/v1/graph-workflows/runs/does-not-exist/explain", json={}, headers=auth_headers)
    assert resp.status_code == 404


# ── fase 13.3: Git sync of definitions ───────────────────────────────────────

@pytest.fixture()
def bare_repo(tmp_path):
    """A local bare repo standing in for a real Git remote — no network,
    no token needed, so the push/pull plumbing is exercised for real."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    return str(remote)


def _clone_and_read(remote: str, tmp_path, file_path: str) -> dict | None:
    work = tmp_path / "check"
    subprocess.run(["git", "clone", remote, str(work)], check=True, capture_output=True)
    target = work / file_path
    return json.loads(target.read_text()) if target.exists() else None


def test_git_sync_push_on_save_commits_the_version(client, auth_headers, bare_repo, tmp_path, monkeypatch):
    monkeypatch.setattr(
        engine.settings, "graph_workflow_git_workdir", str(tmp_path / "workdirs"),
    )
    wf = _make(client, auth_headers, name="git sync wf")
    resp = client.put(
        f"/api/v1/graph-workflows/{wf['id']}/git-sync",
        json={"repo_url": bare_repo, "branch": "main", "token_secret": None, "subpath": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    new_graph = dict(_HTTP_GRAPH)
    new_graph["nodes"] = [*_HTTP_GRAPH["nodes"], {"id": "extra", "type": "manual"}]
    # not a valid extra trigger node but graph validation only cares about the
    # fields actually used here — keep it minimal and just change the url.
    changed_graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "req", "type": "http.request", "params": {"url": "https://example.com", "method": "GET"}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "req"}],
    }
    resp = client.patch(
        f"/api/v1/graph-workflows/{wf['id']}", json={"graph": changed_graph}, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == 2

    committed = _clone_and_read(bare_repo, tmp_path, f"workflows/{wf['id']}.json")
    assert committed is not None
    assert committed["workflow_version"] == 2
    assert committed["graph"]["nodes"][1]["params"]["url"] == "https://example.com"


def test_git_sync_pull_imports_external_change_as_draft_version(
    client, auth_headers, bare_repo, tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        engine.settings, "graph_workflow_git_workdir", str(tmp_path / "workdirs2"),
    )
    wf = _make(client, auth_headers, name="git pull wf")
    resp = client.put(
        f"/api/v1/graph-workflows/{wf['id']}/git-sync",
        json={"repo_url": bare_repo, "branch": "main", "token_secret": None, "subpath": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    # First save pushes v1 and creates the branch/file in the bare repo.
    resp = client.patch(
        f"/api/v1/graph-workflows/{wf['id']}", json={"graph": _HTTP_GRAPH}, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Simulate an external edit landing on the branch (e.g. a PR merge).
    edited_graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "req", "type": "http.request", "params": {"url": "https://external-edit.example", "method": "GET"}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "req"}],
    }
    work = tmp_path / "external"
    subprocess.run(["git", "clone", bare_repo, str(work)], check=True, capture_output=True)
    file_path = work / f"workflows/{wf['id']}.json"
    envelope = json.loads(file_path.read_text())
    envelope["graph"] = edited_graph
    file_path.write_text(json.dumps(envelope))
    subprocess.run(["git", "add", "."], cwd=work, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=ext", "commit", "-m", "external edit"],
        cwd=work, check=True, capture_output=True,
    )
    subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=work, check=True, capture_output=True)

    resp = client.post(f"/api/v1/graph-workflows/{wf['id']}/git-sync/pull", json={}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["unchanged"] is False
    assert len(data["imported_versions"]) == 1
    new_version = data["imported_versions"][0]

    resp = client.get(f"/api/v1/graph-workflows/{wf['id']}/versions/1/diff/{new_version}", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    # The live graph is untouched — only a new draft version was added.
    resp = client.get(f"/api/v1/graph-workflows/{wf['id']}", headers=auth_headers)
    assert resp.json()["graph"]["nodes"][1]["params"]["url"] == "not-a-url"


def test_git_sync_disable_clears_repo_url(client, auth_headers, bare_repo):
    wf = _make(client, auth_headers, name="git disable wf")
    client.put(
        f"/api/v1/graph-workflows/{wf['id']}/git-sync",
        json={"repo_url": bare_repo, "branch": "main", "token_secret": None, "subpath": None},
        headers=auth_headers,
    )
    resp = client.put(
        f"/api/v1/graph-workflows/{wf['id']}/git-sync",
        json={"repo_url": None, "branch": "main", "token_secret": None, "subpath": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["git_sync"] is None


def test_git_sync_pull_without_config_is_a_conflict(client, auth_headers):
    wf = _make(client, auth_headers, name="git no-config wf")
    resp = client.post(f"/api/v1/graph-workflows/{wf['id']}/git-sync/pull", json={}, headers=auth_headers)
    assert resp.status_code == 409
