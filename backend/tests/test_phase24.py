"""
Phase 24 — working examples (24.a workflows, 24.b custom tools).

These are smoke tests: they assert the shipped examples are well-formed, importable,
and internally consistent (declared tools exist; test payloads and URL placeholders
line up with the declared parameters), so a rename or a typo can never silently
strand a "one-click" example. Runs/tool calls against live providers are covered
elsewhere (Phase 18); the optional live-API checks here are opt-in via
``RUN_LIVE_EXAMPLE_TESTS=1``.
"""

import os
import re

import pytest

from app.examples import (
    CUSTOM_TOOL_EXAMPLES,
    WORKFLOW_EXAMPLES,
    list_custom_tool_examples,
    list_workflow_examples,
)
from app.schemas.custom_tools import CustomToolIn
from app.tools.registry import TOOL_DEFINITIONS

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Every built-in tool the model can call, whether enabled by default or not.
_REGISTERED_TOOLS = {t["function"]["name"] for t in TOOL_DEFINITIONS}


def test_examples_are_wellformed():
    assert WORKFLOW_EXAMPLES, "expected at least one example workflow"
    ids = [ex["id"] for ex in WORKFLOW_EXAMPLES]
    assert len(ids) == len(set(ids)), "example ids must be unique"
    for ex in WORKFLOW_EXAMPLES:
        assert ex["id"] and ex["title"] and ex["description"]
        assert ex["goal"].strip(), f"{ex['id']} has an empty goal"
        assert ex["required_tools"], f"{ex['id']} declares no tools"
        assert isinstance(ex["max_steps"], int) and ex["max_steps"] >= 1


def test_example_tools_are_registered():
    """The whole point of the smoke test: declared tools must exist."""
    for ex in WORKFLOW_EXAMPLES:
        for tool in ex["required_tools"]:
            assert tool in _REGISTERED_TOOLS, (
                f"example '{ex['id']}' needs tool '{tool}', which is not in the "
                f"registry ({sorted(_REGISTERED_TOOLS)})"
            )


def test_roadmap_examples_present():
    """The four examples named in roadmap Phase 24.a all ship."""
    ids = {ex["id"] for ex in list_workflow_examples()}
    assert {
        "morning-news-digest",
        "website-watcher",
        "kb-research-report",
        "weather-aware-reminder",
    } <= ids


def test_examples_endpoint(client, auth_headers):
    resp = client.get("/api/v1/workflows/examples", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == len(WORKFLOW_EXAMPLES)
    first = data[0]
    assert {"id", "title", "description", "category", "required_tools", "max_steps", "goal"} <= first.keys()


def test_examples_endpoint_requires_auth(client):
    assert client.get("/api/v1/workflows/examples").status_code == 401


def test_examples_route_not_shadowed_by_run_id(client, auth_headers):
    """`/examples` must resolve to the catalog, not the `/{run_id}` route."""
    resp = client.get("/api/v1/workflows/examples", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ── 24.b — example custom tools ───────────────────────────────────────────────
def test_custom_tool_examples_wellformed():
    assert CUSTOM_TOOL_EXAMPLES, "expected at least one example custom tool"
    ids = [ex["id"] for ex in CUSTOM_TOOL_EXAMPLES]
    assert len(ids) == len(set(ids)), "example ids must be unique"
    names = [ex["tool"]["name"] for ex in CUSTOM_TOOL_EXAMPLES]
    assert len(names) == len(set(names)), "tool names must be unique"


def test_custom_tool_examples_are_valid_definitions():
    """Every example's ``tool`` must pass the same validation as a user import."""
    for ex in CUSTOM_TOOL_EXAMPLES:
        model = CustomToolIn.model_validate(ex["tool"])  # raises on invalid schema/url/name
        assert model.endpoint.url.startswith(("http://", "https://"))


def test_custom_tool_test_payloads_match_parameters():
    """Pre-filled test arguments and URL {placeholders} must be declared params."""
    for ex in CUSTOM_TOOL_EXAMPLES:
        props = set((ex["tool"]["parameters"].get("properties") or {}).keys())
        # every test argument is a declared parameter
        for key in (ex.get("test_arguments") or {}):
            assert key in props, f"{ex['id']}: test arg '{key}' not in parameters"
        # every required parameter is present in the test payload
        for req in ex["tool"]["parameters"].get("required", []):
            assert req in (ex.get("test_arguments") or {}), (
                f"{ex['id']}: required param '{req}' missing from test_arguments"
            )
        # every URL placeholder maps to a declared parameter
        for ph in _PLACEHOLDER.findall(ex["tool"]["endpoint"]["url"]):
            assert ph in props, f"{ex['id']}: URL placeholder '{{{ph}}}' has no parameter"


def test_roadmap_custom_tool_examples_present():
    ids = {ex["id"] for ex in list_custom_tool_examples()}
    assert {
        "currency-convert",
        "wikipedia-summary",
        "public-holidays",
        "geocode",
        "bearer-auth-template",
    } <= ids


def test_bearer_template_shows_auth_config():
    """The template must actually demonstrate header/bearer configuration."""
    tmpl = next(ex for ex in CUSTOM_TOOL_EXAMPLES if ex["id"] == "bearer-auth-template")
    assert tmpl["tool"]["endpoint"]["auth"]["type"] == "bearer"
    assert tmpl["tool"]["endpoint"]["auth"]["token"]


def test_custom_tool_examples_endpoint(client, auth_headers):
    resp = client.get("/api/v1/tools/custom/examples", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == len(CUSTOM_TOOL_EXAMPLES)
    assert {"id", "title", "description", "category", "api", "tool", "test_arguments"} <= data[0].keys()


def test_custom_tool_examples_endpoint_requires_auth(client):
    assert client.get("/api/v1/tools/custom/examples").status_code == 401


def test_example_custom_tools_are_importable(client, auth_headers):
    """Import each example through the real create endpoint (the 'one click')."""
    created_ids = []
    try:
        for ex in CUSTOM_TOOL_EXAMPLES:
            resp = client.post("/api/v1/tools/custom", json=ex["tool"], headers=auth_headers)
            assert resp.status_code == 201, f"{ex['id']}: {resp.text}"
            assert resp.json()["name"] == ex["tool"]["name"]
            created_ids.append(resp.json()["id"])
        # they now appear namespaced in the full tool list
        names = {t["function"]["name"] for t in client.get("/api/v1/tools", headers=auth_headers).json()}
        for ex in CUSTOM_TOOL_EXAMPLES:
            assert f"custom__{ex['tool']['name']}" in names
    finally:
        # keep the shared session DB clean for order-independent tests
        for tid in created_ids:
            client.delete(f"/api/v1/tools/custom/{tid}", headers=auth_headers)


def test_path_param_templating():
    """URL {placeholders} are filled and consumed; the rest ride as query/body."""
    from app.services.custom_tool_service import _apply_path_params

    url, rest = _apply_path_params(
        "https://date.nager.at/api/v3/PublicHolidays/{year}/{countryCode}",
        {"year": 2026, "countryCode": "IT", "extra": "x"},
    )
    assert url == "https://date.nager.at/api/v3/PublicHolidays/2026/IT"
    assert rest == {"extra": "x"}

    # values are URL-encoded
    url2, _ = _apply_path_params(
        "https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
        {"title": "Python (programming language)"},
    )
    assert url2.endswith("/Python%20%28programming%20language%29")

    # an unmatched placeholder is left intact; no args → unchanged
    url3, rest3 = _apply_path_params("https://api.example.com/{missing}", {"a": 1})
    assert url3 == "https://api.example.com/{missing}"
    assert rest3 == {"a": 1}


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_EXAMPLE_TESTS") != "1",
    reason="live external API calls; set RUN_LIVE_EXAMPLE_TESTS=1 to run",
)
def test_example_custom_tools_live(client, auth_headers):
    """Opt-in: import each example and invoke it against its real API.

    ``template`` examples (the bearer scaffold) carry a placeholder token and point
    at a demo host, so we only assert the invocation round-trips, not that the
    upstream authorises it.
    """
    for ex in CUSTOM_TOOL_EXAMPLES:
        created = client.post("/api/v1/tools/custom", json=ex["tool"], headers=auth_headers)
        assert created.status_code == 201, created.text
        tool_id = created.json()["id"]
        try:
            res = client.post(
                f"/api/v1/tools/custom/{tool_id}/test",
                json={"arguments": ex.get("test_arguments") or {}},
                headers=auth_headers,
            )
            assert res.status_code == 200, res.text
            if ex["category"] != "template":
                assert res.json()["ok"], f"{ex['id']} live call failed: {res.json()['result'][:200]}"
        finally:
            client.delete(f"/api/v1/tools/custom/{tool_id}", headers=auth_headers)
