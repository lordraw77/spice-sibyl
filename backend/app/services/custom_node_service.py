"""
Phase 51 (roadmap fase 19) — Custom Node SDK.

Lets users extend the workflow palette themselves without forking the product.
A custom node is a package with a **manifest** (``node.json``) declaring its
type, params/outputs schemas and one of two implementation tiers:

* ``declarative`` — no code: a parameterised ``http.request`` template with
  ``{{param.x}}`` / ``{{input}}`` placeholders. Safe by construction, this is the
  n8n-style declarative node and covers most community connectors.
* ``python`` — a module exposing ``run(params, input, ctx)``; executed **always**
  in the Phase 18 code sandbox (isolated subprocess, CPU/memory/time caps, no
  network), so an uploaded module can never touch the host. ``ctx`` exposes only
  the declared secrets (``ctx.secrets``) and ``ctx.log`` — never the vault.

The engine dispatches ``custom.<name>`` node types here (see
``workflow_graph_service._dispatch``). Output is validated against the manifest's
``outputs`` schema, so a broken node fails loudly and retry / On error apply.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re

import aiosqlite

from app.core.config import settings
from app.db import graph_workflow_repository as repo

# A custom node type is namespaced under ``custom.`` so it can never collide with
# a builtin type (``http.request``, ``llm.*``, …) or a ``tool.*`` node.
_TYPE_RE = re.compile(r"^custom\.[a-z0-9](?:[a-z0-9_.-]{0,62}[a-z0-9])?$")
_VALID_KINDS = frozenset({"declarative", "python"})
_VALID_PERMISSIONS = frozenset({"network", "files", "db"})
_MAX_CODE_BYTES = 256 * 1024
# Placeholder like {{param.token}} / {{input}} / {{input.field}} in a declarative
# request template. Whitespace inside the braces is tolerated.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.\[\]]+)\s*\}\}")


class CustomNodeError(ValueError):
    """A manifest/package problem or a runtime failure inside a custom node."""


# ── manifest validation ──────────────────────────────────────────────────────

def _builtin_types() -> set[str]:
    """Builtin node types a custom type must not shadow. Imported lazily to keep
    this module free of a hard dependency on the (large) engine module."""
    from app.data import node_catalog

    return {n.type for n in node_catalog._STATIC_NODES}


def validate_manifest(manifest: dict, code: str | None) -> dict:
    """Validate a ``node.json`` manifest (and the accompanying ``code`` for a
    python node). Returns a normalised manifest. Raises ``CustomNodeError`` with a
    human-readable reason on any problem (surfaced as an HTTP 400)."""
    if not isinstance(manifest, dict):
        raise CustomNodeError("manifest must be a JSON object")

    node_type = str(manifest.get("type") or "").strip()
    if not _TYPE_RE.match(node_type):
        raise CustomNodeError(
            "manifest.type must match 'custom.<name>' (lowercase letters, digits, "
            "'.', '_', '-'), e.g. 'custom.weather'"
        )
    if node_type in _builtin_types():
        raise CustomNodeError(f"'{node_type}' collides with a builtin node type")

    kind = str(manifest.get("kind") or "declarative").strip()
    if kind not in _VALID_KINDS:
        raise CustomNodeError(f"manifest.kind must be one of {sorted(_VALID_KINDS)}")

    name = str(manifest.get("name") or node_type).strip()
    category = str(manifest.get("category") or "action").strip() or "action"

    params_schema = manifest.get("params")
    if params_schema is not None and not isinstance(params_schema, dict):
        raise CustomNodeError("manifest.params must be a JSON Schema object")
    outputs_schema = manifest.get("outputs")
    if outputs_schema is not None and not isinstance(outputs_schema, dict):
        raise CustomNodeError("manifest.outputs must be a JSON Schema object")

    handles = manifest.get("handles") or ["main"]
    if not isinstance(handles, list) or not all(isinstance(h, str) for h in handles) or not handles:
        raise CustomNodeError("manifest.handles must be a non-empty list of strings")

    secrets = manifest.get("secrets") or []
    if not isinstance(secrets, list) or not all(isinstance(s, str) for s in secrets):
        raise CustomNodeError("manifest.secrets must be a list of secret names")

    permissions = manifest.get("permissions") or []
    if not isinstance(permissions, list) or not (set(permissions) <= _VALID_PERMISSIONS):
        raise CustomNodeError(f"manifest.permissions must be a subset of {sorted(_VALID_PERMISSIONS)}")

    if kind == "declarative":
        request = manifest.get("request")
        if not isinstance(request, dict) or not str(request.get("url") or "").strip():
            raise CustomNodeError("a declarative node requires manifest.request.url")
        if code:
            raise CustomNodeError("a declarative node must not carry code")
    else:  # python
        if not code or not code.strip():
            raise CustomNodeError("a python node requires a code module")
        if len(code.encode("utf-8")) > _MAX_CODE_BYTES:
            raise CustomNodeError(f"code exceeds the {_MAX_CODE_BYTES // 1024} KB limit")
        if "def run" not in code:
            raise CustomNodeError("a python node's code must define 'run(params, input, ctx)'")

    normalised = dict(manifest)
    normalised.update({
        "type": node_type, "name": name, "kind": kind, "category": category,
        "handles": handles, "secrets": secrets, "permissions": permissions,
        "version": manifest.get("version") or "1.0.0",
        "description": str(manifest.get("description") or ""),
        "icon": str(manifest.get("icon") or ""),
    })
    return normalised


def _verify_signature(manifest: dict, code: str | None, signature: str | None) -> None:
    """Roadmap 19.3 — when the workspace requires signed nodes, the package must
    carry a valid HMAC-SHA256 over the canonical manifest + code."""
    if not settings.graph_workflow_require_signed_nodes:
        return
    key = settings.graph_workflow_node_signing_key
    if not key:
        raise CustomNodeError(
            "signed nodes are required but GRAPH_WORKFLOW_NODE_SIGNING_KEY is unset"
        )
    if not signature:
        raise CustomNodeError("this workspace requires a signed package (missing 'signature')")
    if not hmac.compare_digest(signature, sign_package(manifest, code, key)):
        raise CustomNodeError("package signature verification failed")


def sign_package(manifest: dict, code: str | None, key: str) -> str:
    """Deterministic HMAC used both to sign (CLI) and verify (server) a package."""
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n" + (code or "")
    return hmac.new(key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


# ── install / lifecycle ──────────────────────────────────────────────────────

async def install(
    db: aiosqlite.Connection, profile_id: str, manifest: dict, code: str | None = None,
    signature: str | None = None,
) -> dict:
    """Validate and install a package as a new version of its node type."""
    manifest = validate_manifest(manifest, code)
    _verify_signature(manifest, code, signature)
    return await repo.create_custom_node(
        db, profile_id, manifest["type"],
        name=manifest["name"], description=manifest["description"],
        category=manifest["category"], icon=manifest["icon"], kind=manifest["kind"],
        manifest=manifest, code=code if manifest["kind"] == "python" else None,
    )


async def delete(db: aiosqlite.Connection, profile_id: str, node_type: str) -> list[dict]:
    """Delete a custom node type. Returns the list of blocking dependents when
    any workflow still references it (the caller turns that into an HTTP 409)."""
    dependents = await repo.workflows_using_node_type(db, profile_id, node_type)
    if dependents:
        return dependents
    await repo.delete_custom_node(db, profile_id, node_type)
    return []


# ── execution ────────────────────────────────────────────────────────────────

def _lookup(dotted: str, params: dict, node_input) -> object:
    """Resolve a ``param.x`` / ``input`` / ``input.field`` placeholder path."""
    parts = dotted.split(".")
    if parts[0] == "param":
        cur: object = params
        parts = parts[1:]
    elif parts[0] == "input":
        cur = node_input
        parts = parts[1:]
    else:  # bare name → a param
        cur = params
    for p in parts:
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return None
    return cur


def _render(value, params: dict, node_input):
    """Recursively substitute ``{{...}}`` placeholders in a request template.
    A string that is exactly one placeholder keeps the resolved value's type
    (so a number stays a number); mixed strings interpolate as text."""
    if isinstance(value, str):
        m = _PLACEHOLDER_RE.fullmatch(value.strip())
        if m:
            return _lookup(m.group(1), params, node_input)
        return _PLACEHOLDER_RE.sub(
            lambda mo: _stringify(_lookup(mo.group(1), params, node_input)), value
        )
    if isinstance(value, dict):
        return {k: _render(v, params, node_input) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, params, node_input) for v in value]
    return value


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)


def build_declarative_request(manifest: dict, params: dict, node_input) -> dict:
    """Pure mapper (no I/O, unit-testable): a declarative manifest + node params →
    the ``http.request`` spec the engine will issue. Mirrors ``_connector_request``
    so retry / rate-limit / pins all still apply."""
    template = manifest.get("request") or {}
    spec = _render(template, params or {}, node_input)
    if not isinstance(spec, dict):
        raise CustomNodeError("declarative request template did not render to an object")
    for passthrough in ("timeout", "allow_errors", "maxRequestsPerMinute"):
        if (params or {}).get(passthrough) is not None:
            spec[passthrough] = params[passthrough]
    return spec


def _wrap_python(code: str, params: dict, node_input, secrets: dict) -> str:
    """Build the sandbox script: materialise params/input/ctx, call ``run`` and
    print the JSON result on a sentinel line the caller parses out of stdout."""
    payload = json.dumps(
        {"params": params, "input": node_input, "secrets": secrets}, default=str
    )
    # NB: the sandbox stubs out ``socket`` (no network), which makes ``import
    # asyncio`` fail — so we never import it. ``run`` is normally synchronous; a
    # coroutine (no real I/O is possible in the sandbox anyway) is driven by hand.
    return (
        "import json\n"
        f"_ctx_data = json.loads({payload!r})\n"
        "class _Ctx:\n"
        "    def __init__(self, secrets):\n"
        "        self.secrets = secrets\n"
        "        self.logs = []\n"
        "    def log(self, *a):\n"
        "        self.logs.append(' '.join(str(x) for x in a))\n"
        "params = _ctx_data['params']\n"
        "input = _ctx_data['input']\n"
        "ctx = _Ctx(_ctx_data['secrets'])\n"
        "\n" + code + "\n"
        "\n"
        "_res = run(params, input, ctx)\n"
        "if hasattr(_res, '__await__'):\n"
        "    _it = _res.__await__()\n"
        "    try:\n"
        "        while True:\n"
        "            _it.send(None)\n"
        "    except StopIteration as _stop:\n"
        "        _res = _stop.value\n"
        "print('__SIBYL_CUSTOM_NODE_RESULT__' + json.dumps({'output': _res, 'logs': ctx.logs}, default=str))\n"
    )


async def _run_python(manifest: dict, code: str, params: dict, node_input, ctx: dict) -> dict:
    """Execute a python custom node in the code sandbox. Declared secrets that the
    graph author bound (as resolved params named after the secret) are handed to
    ``ctx.secrets``; nothing from the vault is exposed."""
    from app.tools.code_interpreter import python_exec

    declared = manifest.get("secrets") or []
    # Secrets travel as resolved params (author wired {{ $secrets.NAME }}); we hand
    # the node only the ones it declared, never the raw vault.
    secrets = {name: (params or {}).get(name) for name in declared}
    script = _wrap_python(code, params or {}, node_input, secrets)
    stdout = await python_exec(script)
    marker = "__SIBYL_CUSTOM_NODE_RESULT__"
    for line in reversed(stdout.splitlines()):
        if marker in line:
            try:
                parsed = json.loads(line.split(marker, 1)[1])
            except ValueError as exc:
                raise CustomNodeError(f"custom node returned invalid JSON: {exc}") from exc
            return parsed.get("output")
    raise CustomNodeError(f"custom node '{manifest['type']}' produced no result:\n{stdout[:500]}")


async def execute(
    db: aiosqlite.Connection, profile_id: str, node_type: str, params: dict, node_input, ctx: dict,
) -> tuple[object, list[str]]:
    """Dispatch entry point for a ``custom.<name>`` node. Returns
    ``(output, active_handles)``; output is validated against the manifest's
    ``outputs`` schema when one is declared."""
    node = await repo.get_custom_node(db, profile_id, node_type)
    if node is None:
        raise CustomNodeError(f"custom node '{node_type}' is not installed")
    if not node["enabled"]:
        raise CustomNodeError(f"custom node '{node_type}' is disabled")

    manifest = node["manifest"]
    if node["kind"] == "declarative":
        from app.services import workflow_graph_service as engine

        spec = build_declarative_request(manifest, params, node_input)
        output = await engine._exec_http_request(spec)
        output = {**output, "_customNode": node_type}
    else:
        output = await _run_python(manifest, node["code"] or "", params, node_input, ctx)

    schema = manifest.get("outputs")
    if isinstance(schema, dict) and schema:
        from app.services import workflow_graph_service as engine

        errors = engine._validate_json_schema(output, schema)
        if errors:
            raise CustomNodeError(
                f"custom node '{node_type}' output failed its schema: {'; '.join(errors[:3])}"
            )
    handles = manifest.get("handles") or ["main"]
    return output, ["main"] if "main" in handles else [handles[0]]
