"""
Phase 50 — roadmap fase 18: LLM quality.

Covers the ``llm.judge`` quality-gate node (18.1) — rubric scoring, the
pass/fail handle routing by threshold, the default 60%-of-scale threshold and
the required-criteria guard — and prompt A/B testing (18.2): round-robin /
weighted variant selection, the ``_variant`` stamp on node outputs and the
per-variant metrics endpoint with a winner flag.

The LLM layer is stubbed (``_cached_complete``); runs are driven exactly like
test_phase35 (``_spawn`` intercepted, ``_execute`` awaited by the test).
"""

import asyncio

import pytest

from app.db import graph_workflow_repository as repo
from app.services import workflow_graph_service as engine


@pytest.fixture()
def captured_spawns(monkeypatch):
    spawns: list[tuple] = []
    monkeypatch.setattr(engine, "_spawn", lambda *args, **kwargs: spawns.append((args, kwargs)))
    return spawns


def _drive_last_run(spawns):
    args, kwargs = spawns[-1]
    asyncio.run(engine._execute(*args, **kwargs))


def _fake_llm(monkeypatch, content: str):
    async def _fake(request):
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        }, "miss"

    monkeypatch.setattr("app.workflow.nodes.llm._cached_complete", _fake)


def _make(client, auth_headers, graph, name="phase50 flow"):
    return client.post(
        "/api/v1/graph-workflows", json={"name": name, "graph": graph}, headers=auth_headers
    ).json()


def _run(client, auth_headers, wf, payload=None):
    return client.post(
        f"/api/v1/graph-workflows/{wf['id']}/run", json={"payload": payload or {}}, headers=auth_headers
    ).json()["run_id"]


def _run_and_drive(client, auth_headers, wf, spawns, payload=None):
    run_id = _run(client, auth_headers, wf, payload)
    _drive_last_run(spawns)
    return client.get(f"/api/v1/graph-workflows/runs/{run_id}", headers=auth_headers).json()


def _outputs(run):
    return {nr["node_id"]: nr for nr in run["node_runs"]}


# ── catalog ─────────────────────────────────────────────────────────────────

def test_catalog_includes_judge_and_ab_params(client, auth_headers):
    catalog = {t["type"]: t for t in client.get(
        "/api/v1/graph-workflows/node-types", headers=auth_headers
    ).json()}
    judge = catalog["llm.judge"]
    assert judge["category"] == "ai"
    assert judge["outputs"] == ["pass", "fail"]
    # A/B params are advertised on the llm.* nodes.
    for t in ("llm.judge", "llm.completion", "llm.classify", "llm.extract"):
        names = {p["name"] for p in catalog[t]["params_schema"]}
        assert {"variants", "variantStrategy"} <= names


# ── 18.1 llm.judge ──────────────────────────────────────────────────────────

def _judge_graph(judge_params):
    return {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "j", "type": "llm.judge", "params": judge_params},
            {"id": "pass", "type": "set", "params": {"fields": {"ok": True}}},
            {"id": "fail", "type": "set", "params": {"fields": {"ok": False}}},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "j"},
            {"id": "e2", "source": "j", "sourceHandle": "pass", "target": "pass"},
            {"id": "e3", "source": "j", "sourceHandle": "fail", "target": "fail"},
        ],
    }


def test_judge_passes_and_routes_to_pass_handle(client, auth_headers, captured_spawns, monkeypatch):
    _fake_llm(monkeypatch, '{"score": 5, "verdict": "pass", "rationale": "excellent"}')
    wf = _make(client, auth_headers, _judge_graph(
        {"input": "some draft", "criteria": "clear and specific", "scaleMax": 5, "threshold": 4}
    ))
    run = _run_and_drive(client, auth_headers, wf, captured_spawns)
    assert run["status"] == "completed", run
    out = _outputs(run)
    assert out["j"]["output"]["passed"] is True
    assert out["j"]["output"]["verdict"] == "pass"
    assert out["j"]["output"]["score"] == 5
    assert out["pass"]["status"] == "ok"
    assert out["fail"]["status"] == "skipped"


def test_judge_fails_below_threshold_and_routes_to_fail_handle(client, auth_headers, captured_spawns, monkeypatch):
    # Model claims "pass", but the score is below the threshold — the threshold wins.
    _fake_llm(monkeypatch, '{"score": 2, "verdict": "pass", "rationale": "weak"}')
    wf = _make(client, auth_headers, _judge_graph(
        {"input": "some draft", "criteria": "clear and specific", "scaleMax": 5, "threshold": 4}
    ))
    run = _run_and_drive(client, auth_headers, wf, captured_spawns)
    assert run["status"] == "completed", run
    out = _outputs(run)
    assert out["j"]["output"]["passed"] is False
    assert out["j"]["output"]["verdict"] == "fail"
    assert out["fail"]["status"] == "ok"
    assert out["pass"]["status"] == "skipped"


def test_judge_default_threshold_is_60pct_of_scale(client, auth_headers, captured_spawns, monkeypatch):
    # scaleMax 10, no threshold → default 6. Score 6 passes, 5 would fail.
    _fake_llm(monkeypatch, '{"score": 6, "rationale": "good enough"}')
    wf = _make(client, auth_headers, _judge_graph(
        {"input": "x", "criteria": "quality", "scaleMax": 10}
    ))
    run = _run_and_drive(client, auth_headers, wf, captured_spawns)
    out = _outputs(run)
    assert out["j"]["output"]["threshold"] == 6.0
    assert out["j"]["output"]["passed"] is True


def test_judge_clamps_out_of_range_score(client, auth_headers, captured_spawns, monkeypatch):
    _fake_llm(monkeypatch, '{"score": 99, "rationale": "over the top"}')
    wf = _make(client, auth_headers, _judge_graph(
        {"input": "x", "criteria": "quality", "scaleMax": 5}
    ))
    run = _run_and_drive(client, auth_headers, wf, captured_spawns)
    assert _outputs(run)["j"]["output"]["score"] == 5


def test_judge_requires_criteria(client, auth_headers, captured_spawns, monkeypatch):
    _fake_llm(monkeypatch, '{"score": 5}')
    wf = _make(client, auth_headers, _judge_graph({"input": "x", "criteria": ""}))
    run = _run_and_drive(client, auth_headers, wf, captured_spawns)
    assert run["status"] == "failed"
    assert "criteria" in (run["error"] or "")


def test_judge_rejects_non_numeric_score(client, auth_headers, captured_spawns, monkeypatch):
    _fake_llm(monkeypatch, '{"verdict": "pass"}')
    wf = _make(client, auth_headers, _judge_graph({"input": "x", "criteria": "quality"}))
    run = _run_and_drive(client, auth_headers, wf, captured_spawns)
    assert run["status"] == "failed"
    assert "score" in (run["error"] or "")


# ── 18.2 prompt A/B testing ─────────────────────────────────────────────────

def _ab_graph(variants, strategy="round-robin"):
    return {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "c", "type": "llm.completion",
             "params": {"prompt": "base", "variants": variants, "variantStrategy": strategy}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "c"}],
    }


def test_round_robin_variants_alternate_across_runs(client, auth_headers, captured_spawns, monkeypatch):
    _fake_llm(monkeypatch, '{"content": "hi"}')  # llm.completion tolerates any dict/text
    variants = [
        {"name": "concise", "params": {"prompt": "short"}},
        {"name": "detailed", "params": {"prompt": "long"}},
    ]
    wf = _make(client, auth_headers, _ab_graph(variants))
    seen = []
    for _ in range(4):
        run = _run_and_drive(client, auth_headers, wf, captured_spawns)
        assert run["status"] == "completed", run
        seen.append(_outputs(run)["c"]["output"]["_variant"])
    # Even round-robin: two of each, alternating.
    assert seen == ["concise", "detailed", "concise", "detailed"]


def test_variant_metrics_endpoint_breaks_down_and_flags_winner(client, auth_headers, captured_spawns, monkeypatch):
    # A judge node A/B-tested: variant "strict" scores low, "lenient" scores high.
    replies = {"strict": '{"score": 2, "rationale": "harsh"}', "lenient": '{"score": 5, "rationale": "kind"}'}
    state = {"n": 0}

    async def _fake(request):
        # round-robin order is strict, lenient, strict, lenient...
        which = "strict" if state["n"] % 2 == 0 else "lenient"
        state["n"] += 1
        return {
            "choices": [{"message": {"content": replies[which]}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        }, "miss"

    monkeypatch.setattr("app.workflow.nodes.llm._cached_complete", _fake)
    variants = [
        {"name": "strict", "params": {"instructions": "be harsh"}},
        {"name": "lenient", "params": {"instructions": "be kind"}},
    ]
    graph = {
        "nodes": [
            {"id": "t", "type": "manual"},
            {"id": "j", "type": "llm.judge",
             "params": {"input": "draft", "criteria": "quality", "scaleMax": 5,
                        "variants": variants, "variantStrategy": "round-robin"}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "j"}],
    }
    wf = _make(client, auth_headers, graph)
    for _ in range(4):
        _run_and_drive(client, auth_headers, wf, captured_spawns)

    stats = client.get(
        f"/api/v1/graph-workflows/{wf['id']}/nodes/j/variants", headers=auth_headers
    ).json()
    by = {s["variant"]: s for s in stats}
    assert set(by) == {"strict", "lenient"}
    assert by["strict"]["executions"] == 2
    assert by["strict"]["avg_score"] == 2.0
    assert by["lenient"]["avg_score"] == 5.0
    assert by["lenient"]["winner"] is True
    assert by["strict"]["winner"] is False


def test_weighted_variant_with_zero_weights_falls_back_to_uniform(client, auth_headers, captured_spawns, monkeypatch):
    _fake_llm(monkeypatch, '{"content": "hi"}')
    variants = [
        {"name": "a", "weight": 0, "params": {"prompt": "a"}},
        {"name": "b", "weight": 0, "params": {"prompt": "b"}},
    ]
    wf = _make(client, auth_headers, _ab_graph(variants, strategy="weighted"))
    run = _run_and_drive(client, auth_headers, wf, captured_spawns)
    assert run["status"] == "completed", run
    assert _outputs(run)["c"]["output"]["_variant"] in {"a", "b"}


def test_variant_list_tolerates_json_string_and_drops_malformed():
    good = [{"name": "x", "params": {"a": 1}}]
    assert engine._variant_list({"variants": good}) == good
    # a JSON string (raw inspector field) is parsed
    assert engine._variant_list({"variants": '[{"name":"x","params":{"a":1}}]'}) == good
    # malformed entries (no params dict) are dropped
    assert engine._variant_list({"variants": [{"name": "x"}, "nope", 3]}) == []
    assert engine._variant_list({"variants": "not json"}) == []
    assert engine._variant_list({}) == []
