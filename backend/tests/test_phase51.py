"""
Phase 51 — roadmap fase 19: Custom Node SDK.

Covers manifest validation and packaging (19.1), the upload/registry/lifecycle
CRUD with versioning and dependent-blocking deletes (19.2), the security model
(19.3 — declarative safe by construction, python always in the sandbox, optional
signing), and distribution (19.5 — a workflow export lists its custom-node
dependencies). Full runs use ``run_workflow_sync`` so they execute inline.
"""

import asyncio
import json

import pytest

from app.db import graph_workflow_repository as repo
from app.services import custom_node_service as cns
from app.services import workflow_graph_service as engine


def _make_wf(client, auth_headers, name, graph, **extra):
    resp = client.post(
        "/api/v1/graph-workflows", json={"name": name, "graph": graph, **extra},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _run_sync(wf, payload=None):
    async def _call():
        db = await engine._connect()
        try:
            return await engine.run_workflow_sync(
                db, wf["id"], wf["profile_id"], trigger_payload=payload or {}
            )
        finally:
            await db.close()
    return asyncio.run(_call())


_DECLARATIVE = {
    "type": "custom.httpbin",
    "name": "HTTPBin GET",
    "kind": "declarative",
    "category": "custom",
    "description": "Calls httpbin.",
    "params": {"type": "object", "properties": {"q": {"type": "string"}}},
    "handles": ["main"],
    "request": {"method": "GET", "url": "https://httpbin.org/get", "query": {"term": "{{param.q}}"}},
}

_PYTHON = {
    "type": "custom.double",
    "name": "Double",
    "kind": "python",
    "category": "custom",
    "params": {"type": "object", "properties": {"value": {"type": "number"}}},
    "outputs": {"type": "object", "properties": {"doubled": {"type": "number"}}, "required": ["doubled"]},
    "handles": ["main"],
}
_PYTHON_CODE = "def run(params, input, ctx):\n    return {'doubled': (params.get('value') or 0) * 2}\n"


# ── manifest validation (19.1) ───────────────────────────────────────────────

def test_validate_rejects_non_namespaced_type():
    with pytest.raises(cns.CustomNodeError):
        cns.validate_manifest({"type": "http.request", "kind": "declarative",
                               "request": {"url": "https://x"}}, None)


def test_validate_rejects_builtin_collision():
    with pytest.raises(cns.CustomNodeError):
        cns.validate_manifest({"type": "custom.x", "kind": "declarative"}, None)  # no request.url


def test_validate_python_requires_run():
    with pytest.raises(cns.CustomNodeError):
        cns.validate_manifest({"type": "custom.x", "kind": "python"}, "x = 1\n")


def test_validate_declarative_must_not_carry_code():
    with pytest.raises(cns.CustomNodeError):
        cns.validate_manifest(_DECLARATIVE, "def run(): pass")


def test_build_declarative_request_renders_placeholders():
    spec = cns.build_declarative_request(_DECLARATIVE, {"q": "hello"}, None)
    assert spec["url"] == "https://httpbin.org/get"
    assert spec["query"] == {"term": "hello"}


def test_render_keeps_scalar_type():
    manifest = {**_DECLARATIVE, "request": {"url": "https://x", "body": {"n": "{{param.n}}"}}}
    spec = cns.build_declarative_request(manifest, {"n": 42}, None)
    assert spec["body"]["n"] == 42  # stayed an int, not "42"


# ── install / registry / versioning (19.2) ───────────────────────────────────

def test_install_and_list_custom_node(client, auth_headers):
    resp = client.post("/api/v1/graph-workflows/custom-nodes",
                       json={"manifest": _DECLARATIVE}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["version"] == 1
    listed = client.get("/api/v1/graph-workflows/custom-nodes", headers=auth_headers).json()
    assert any(n["type"] == "custom.httpbin" for n in listed)


def test_new_version_increments(client, auth_headers):
    m = {**_DECLARATIVE, "type": "custom.versioned"}
    client.post("/api/v1/graph-workflows/custom-nodes", json={"manifest": m}, headers=auth_headers)
    resp = client.post("/api/v1/graph-workflows/custom-nodes/custom.versioned/versions",
                       json={"manifest": {**m, "version": "2.0.0"}}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["version"] == 2
    versions = client.get("/api/v1/graph-workflows/custom-nodes/custom.versioned/versions",
                          headers=auth_headers).json()
    assert [v["version"] for v in versions] == [2, 1]


def test_custom_node_appears_in_palette(client, auth_headers):
    m = {**_DECLARATIVE, "type": "custom.palette"}
    client.post("/api/v1/graph-workflows/custom-nodes", json={"manifest": m}, headers=auth_headers)
    catalog = {t["type"]: t for t in client.get(
        "/api/v1/graph-workflows/node-types", headers=auth_headers).json()}
    assert "custom.palette" in catalog
    assert catalog["custom.palette"]["custom"] is True


def test_disabled_node_hidden_from_palette(client, auth_headers):
    m = {**_DECLARATIVE, "type": "custom.toggle"}
    client.post("/api/v1/graph-workflows/custom-nodes", json={"manifest": m}, headers=auth_headers)
    client.patch("/api/v1/graph-workflows/custom-nodes/custom.toggle",
                 json={"enabled": False}, headers=auth_headers)
    catalog = {t["type"] for t in client.get(
        "/api/v1/graph-workflows/node-types", headers=auth_headers).json()}
    assert "custom.toggle" not in catalog


def test_delete_blocked_by_dependent_workflow(client, auth_headers):
    m = {**_DECLARATIVE, "type": "custom.used"}
    client.post("/api/v1/graph-workflows/custom-nodes", json={"manifest": m}, headers=auth_headers)
    graph = {"nodes": [
        {"id": "t", "type": "manual", "position": {"x": 0, "y": 0}},
        {"id": "c", "type": "custom.used", "position": {"x": 1, "y": 0}, "params": {"q": "x"}},
    ], "edges": [{"id": "e", "source": "t", "target": "c"}]}
    _make_wf(client, auth_headers, "uses-custom", graph)
    resp = client.delete("/api/v1/graph-workflows/custom-nodes/custom.used", headers=auth_headers)
    assert resp.status_code == 409
    assert resp.json()["detail"]["dependents"]


def test_delete_succeeds_without_dependents(client, auth_headers):
    m = {**_DECLARATIVE, "type": "custom.unused"}
    client.post("/api/v1/graph-workflows/custom-nodes", json={"manifest": m}, headers=auth_headers)
    resp = client.delete("/api/v1/graph-workflows/custom-nodes/custom.unused", headers=auth_headers)
    assert resp.status_code == 204


# ── python execution in the sandbox (19.3) ───────────────────────────────────

def test_python_custom_node_executes(client, auth_headers):
    if not engine.settings.code_interpreter_enabled:
        pytest.skip("code interpreter disabled")
    client.post("/api/v1/graph-workflows/custom-nodes",
                json={"manifest": _PYTHON, "code": _PYTHON_CODE}, headers=auth_headers)
    graph = {"nodes": [
        {"id": "t", "type": "manual", "position": {"x": 0, "y": 0}},
        {"id": "d", "type": "custom.double", "position": {"x": 1, "y": 0}, "params": {"value": 21}},
    ], "edges": [{"id": "e", "source": "t", "target": "d"}]}
    wf = _make_wf(client, auth_headers, "doubler", graph)
    run = _run_sync(wf)
    assert run["status"] == "completed", run
    assert run["output"]["doubled"] == 42


# ── signing (19.3) ────────────────────────────────────────────────────────────

def test_sign_and_verify_roundtrip():
    sig = cns.sign_package(_DECLARATIVE, None, "key")
    assert sig == cns.sign_package(_DECLARATIVE, None, "key")
    assert sig != cns.sign_package(_DECLARATIVE, None, "other")


# ── distribution (19.5) ──────────────────────────────────────────────────────

def test_export_lists_custom_node_dependencies(client, auth_headers):
    m = {**_DECLARATIVE, "type": "custom.dep"}
    client.post("/api/v1/graph-workflows/custom-nodes", json={"manifest": m}, headers=auth_headers)
    graph = {"nodes": [
        {"id": "t", "type": "manual", "position": {"x": 0, "y": 0}},
        {"id": "c", "type": "custom.dep", "position": {"x": 1, "y": 0}, "params": {"q": "x"}},
    ], "edges": [{"id": "e", "source": "t", "target": "c"}]}
    wf = _make_wf(client, auth_headers, "export-deps", graph)
    export = client.get(f"/api/v1/graph-workflows/{wf['id']}/export", headers=auth_headers).json()
    deps = {d["type"]: d["version"] for d in export["custom_nodes"]}
    assert deps == {"custom.dep": 1}
