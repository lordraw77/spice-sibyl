"""
Phase 41 — OpenAPI import (roadmap fase 9.4).

Turn an OpenAPI (Swagger) spec into a set of preconfigured ``http.request``
node drafts — one per operation — so integrating a REST service costs a paste
instead of hand-wiring every endpoint. The nodes are returned to the editor
(NOT saved); the user drops the ones they need onto the canvas.

Auth declared by the spec's ``securitySchemes`` is mapped onto ``$secrets``
placeholders (a bearer token → ``Authorization: Bearer {{ $secrets.API_TOKEN }}``,
an apiKey header → that header ``= {{ $secrets.<SCHEME> }}``) rather than being
hard-coded, following the fase 1.3 credentials rule.
"""

import logging
import re

import httpx

from app.core.config import settings
from app.schemas.graph_workflows import GraphNode, OpenApiOperationOut

logger = logging.getLogger(__name__)

_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
_MAX_SPEC_BYTES = 5 * 1024 * 1024
_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


async def fetch_spec(url: str) -> dict:
    """Download and parse a spec from a URL (JSON, or YAML when PyYAML is present)."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    raw = resp.text
    if len(raw.encode("utf-8", "ignore")) > _MAX_SPEC_BYTES:
        raise ValueError("spec is too large")
    return _parse_spec(raw, url)


def _parse_spec(raw: str, url: str = "") -> dict:
    import json

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    try:
        import yaml  # optional dependency
    except ImportError as exc:  # pragma: no cover
        raise ValueError("spec is not valid JSON and PyYAML is not installed for YAML parsing") from exc
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("spec did not parse to an object")
    return data


def _base_url(spec: dict) -> str:
    servers = spec.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        return str(servers[0].get("url") or "").rstrip("/")
    # Swagger 2.0 fallback.
    host = spec.get("host")
    if host:
        scheme = (spec.get("schemes") or ["https"])[0]
        base = spec.get("basePath") or ""
        return f"{scheme}://{host}{base}".rstrip("/")
    return ""


def _auth_headers(spec: dict) -> dict:
    """Header placeholders derived from the spec's security schemes."""
    comps = spec.get("components") or {}
    schemes = comps.get("securitySchemes") or spec.get("securityDefinitions") or {}
    headers: dict = {}
    for scheme_name, scheme in schemes.items():
        if not isinstance(scheme, dict):
            continue
        stype = (scheme.get("type") or "").lower()
        if stype == "http" and (scheme.get("scheme") or "").lower() == "bearer":
            headers["Authorization"] = "=Bearer {{ $secrets.API_TOKEN }}"
        elif stype == "apikey" and (scheme.get("in") or "").lower() == "header":
            hdr = scheme.get("name") or "X-API-Key"
            secret = _SLUG_RE.sub("_", str(scheme_name)).upper().strip("_") or "API_KEY"
            headers[hdr] = f"={{{{ $secrets.{secret} }}}}"
    return headers


def _slug(operation_id: str, method: str, path: str) -> str:
    base = operation_id or f"{method}_{path}"
    slug = _SLUG_RE.sub("_", base).strip("_").lower()
    return slug[:48] or "op"


def _operation_node(
    base_url: str, method: str, path: str, op: dict, auth_headers: dict, index: int
) -> OpenApiOperationOut:
    op_id = str(op.get("operationId") or "").strip() or f"{method}_{path}"
    slug = _slug(str(op.get("operationId") or ""), method, path)
    node_id = f"op_{slug}_{index}"

    params = op.get("parameters") or []
    query: dict = {}
    for p in params:
        if not isinstance(p, dict):
            continue
        where = (p.get("in") or "").lower()
        pname = p.get("name")
        if not pname:
            continue
        if where == "query":
            query[pname] = ""  # user fills in / wires an expression

    node_params: dict = {
        "method": method.upper(),
        "url": base_url + path,  # {param} path placeholders left for the user
    }
    if query:
        node_params["query"] = query
    if auth_headers:
        node_params["headers"] = dict(auth_headers)
    if method.lower() in ("post", "put", "patch") and op.get("requestBody"):
        node_params["body"] = {}

    node = GraphNode(
        id=node_id,
        type="http.request",
        name=(op.get("summary") or op_id)[:80],
        params=node_params,
        position={"x": 240.0, "y": float(120 + index * 120)},
        # fase 2.1 retry preset for http.request (matches the catalog default).
        retry=2, backoff=2.0, backoffStrategy="exponential", timeoutMs=60000,
    )
    return OpenApiOperationOut(
        operation_id=op_id,
        method=method.upper(),
        path=path,
        summary=str(op.get("summary") or "")[:200],
        node=node,
    )


def build_operations(spec: dict, path_prefix: str = "") -> tuple[str, str, list[OpenApiOperationOut], list[str]]:
    """Parse a spec dict → (api_title, base_url, [operation nodes], warnings)."""
    if not isinstance(spec, dict) or "paths" not in spec:
        raise ValueError("not an OpenAPI spec (no 'paths')")
    title = str((spec.get("info") or {}).get("title") or "API")
    base_url = _base_url(spec)
    auth_headers = _auth_headers(spec)
    warnings: list[str] = []
    if not base_url:
        warnings.append("No server URL in the spec — set each node's URL host manually.")
    if not auth_headers and (spec.get("components", {}).get("securitySchemes") or spec.get("securityDefinitions")):
        warnings.append("The spec declares auth this importer can't map automatically — set headers by hand.")

    max_ops = max(1, int(settings.graph_workflow_openapi_max_operations))
    operations: list[OpenApiOperationOut] = []
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        if path_prefix and not str(path).startswith(path_prefix):
            continue
        for method in _HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            if len(operations) >= max_ops:
                warnings.append(f"Import capped at {max_ops} operations; some were skipped.")
                return title, base_url, operations, warnings
            operations.append(_operation_node(base_url, method, str(path), op, auth_headers, len(operations)))
    if not operations:
        warnings.append("No operations matched — check the path filter.")
    return title, base_url, operations, warnings
