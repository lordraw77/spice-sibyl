"""
Phase 46 — roadmap fase 14.5: the ``sibyl-wf`` CLI.

A thin, dependency-light client over the existing graph-workflow REST API —
no direct database access, so it works identically against a local dev
server or a remote deployment. Serves CI (workflow tests in pipelines, fase
13.3's Git-synced definitions) and UI-less operations.

    python -m app.cli.sibyl_wf run <workflow_id> [--trigger payload.json]
    python -m app.cli.sibyl_wf export <workflow_id> [--out file.json]
    python -m app.cli.sibyl_wf import <file.json>
    python -m app.cli.sibyl_wf test <workflow_id> <node_id> [--input input.json]
    python -m app.cli.sibyl_wf logs <run_id>

Auth: API-key auth via ``SIBYL_API_KEY`` (or ``--api-key``) — a bearer access
token (e.g. minted by ``POST /auth/login``), sent as ``Authorization: Bearer
<key>``. ``SIBYL_API_URL`` (or ``--api-url``, default
``http://localhost:8000/api/v1``) and ``SIBYL_PROFILE_ID`` (or
``--profile-id``, optional — ``X-Profile-ID``) round out the connection.

Built on ``httpx.AsyncClient`` (rather than the sync client) so the exact
same code path is exercisable in-process against the ASGI app in tests (no
real network) as well as against a real deployment over HTTP.
"""

import argparse
import asyncio
import json
import os
import sys

import httpx

_DEFAULT_API_URL = "http://localhost:8000/api/v1"


def _client(api_url: str, api_key: str, profile_id: str | None) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {api_key}"}
    if profile_id:
        headers["X-Profile-ID"] = profile_id
    return httpx.AsyncClient(base_url=api_url.rstrip("/"), headers=headers, timeout=30.0)


def _load_json_arg(path: str | None) -> dict:
    if not path:
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a JSON object")
    return data


def _print(resp: httpx.Response) -> int:
    try:
        body = resp.json()
    except ValueError:
        body = resp.text
    print(json.dumps(body, indent=2, default=str) if isinstance(body, (dict, list)) else body)
    return 0 if resp.is_success else 1


async def cmd_run(client: httpx.AsyncClient, args: argparse.Namespace) -> int:
    payload = _load_json_arg(args.trigger)
    resp = await client.post(f"/graph-workflows/{args.workflow_id}/run", json={"payload": payload})
    return _print(resp)


async def cmd_export(client: httpx.AsyncClient, args: argparse.Namespace) -> int:
    resp = await client.get(f"/graph-workflows/{args.workflow_id}/export")
    if not resp.is_success:
        return _print(resp)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(resp.json(), fh, indent=2, default=str)
        print(f"wrote {args.out}")
        return 0
    return _print(resp)


async def cmd_import(client: httpx.AsyncClient, args: argparse.Namespace) -> int:
    with open(args.file, encoding="utf-8") as fh:
        body = json.load(fh)
    resp = await client.post("/graph-workflows/import", json=body)
    return _print(resp)


async def cmd_test(client: httpx.AsyncClient, args: argparse.Namespace) -> int:
    body: dict = {}
    if args.input is not None:
        with open(args.input, encoding="utf-8") as fh:
            body["input"] = json.load(fh)
    resp = await client.post(f"/graph-workflows/{args.workflow_id}/nodes/{args.node_id}/test", json=body)
    return _print(resp)


async def cmd_logs(client: httpx.AsyncClient, args: argparse.Namespace) -> int:
    resp = await client.get(f"/graph-workflows/runs/{args.run_id}")
    return _print(resp)


# ── custom-node authoring (Phase 51 / roadmap fase 19.4) ─────────────────────

_DECLARATIVE_MANIFEST = {
    "type": "custom.example",
    "name": "Example connector",
    "kind": "declarative",
    "category": "custom",
    "description": "Calls an HTTP API and returns its JSON body.",
    "version": "1.0.0",
    "params": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    "outputs": {"type": "object"},
    "handles": ["main"],
    "secrets": [],
    "permissions": ["network"],
    "request": {
        "method": "GET",
        "url": "https://api.example.com/search",
        "query": {"q": "{{param.query}}"},
    },
}
_PYTHON_MANIFEST = {
    "type": "custom.example",
    "name": "Example node",
    "kind": "python",
    "category": "custom",
    "description": "A python node run in the sandbox.",
    "version": "1.0.0",
    "params": {"type": "object", "properties": {"value": {"type": "number"}}},
    "outputs": {"type": "object", "properties": {"doubled": {"type": "number"}}},
    "handles": ["main"],
    "secrets": [],
    "permissions": [],
}
_PYTHON_MODULE = (
    "def run(params, input, ctx):\n"
    "    \"\"\"Double the incoming value. `ctx.secrets` holds declared secrets;\n"
    "    `ctx.log(...)` records a troubleshooting line.\"\"\"\n"
    "    value = params.get('value') or 0\n"
    "    ctx.log('doubling', value)\n"
    "    return {'doubled': value * 2}\n"
)


def _read_package(directory: str) -> tuple[dict, str | None]:
    with open(os.path.join(directory, "node.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    code = None
    module_path = os.path.join(directory, "module.py")
    if os.path.exists(module_path):
        with open(module_path, encoding="utf-8") as fh:
            code = fh.read()
    return manifest, code


async def cmd_node_init(client: httpx.AsyncClient, args: argparse.Namespace) -> int:  # noqa: ARG001
    """Scaffold node.json + (python) module.py + a fixture into a directory."""
    os.makedirs(args.directory, exist_ok=True)
    manifest = dict(_PYTHON_MANIFEST if args.kind == "python" else _DECLARATIVE_MANIFEST)
    manifest["type"] = args.type
    with open(os.path.join(args.directory, "node.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    if args.kind == "python":
        with open(os.path.join(args.directory, "module.py"), "w", encoding="utf-8") as fh:
            fh.write(_PYTHON_MODULE)
    with open(os.path.join(args.directory, "fixture.json"), "w", encoding="utf-8") as fh:
        json.dump({"params": {}, "input": None}, fh, indent=2)
    print(f"scaffolded {args.kind} custom node '{args.type}' in {args.directory}")
    return 0


async def cmd_node_test(client: httpx.AsyncClient, args: argparse.Namespace) -> int:  # noqa: ARG001
    """Validate the package locally against the manifest contract (fase 19.4/3.1);
    for a declarative node, render the http.request spec from the fixture."""
    from app.services import custom_node_service

    manifest, code = _read_package(args.directory)
    try:
        manifest = custom_node_service.validate_manifest(manifest, code)
    except custom_node_service.CustomNodeError as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        return 1
    print(f"ok: manifest for '{manifest['type']}' ({manifest['kind']}) is valid")
    fixture_path = os.path.join(args.directory, "fixture.json")
    fixture = json.load(open(fixture_path, encoding="utf-8")) if os.path.exists(fixture_path) else {}
    if manifest["kind"] == "declarative":
        spec = custom_node_service.build_declarative_request(
            manifest, fixture.get("params") or {}, fixture.get("input")
        )
        print("rendered http.request:")
        print(json.dumps(spec, indent=2, default=str))
    return 0


async def cmd_node_pack(client: httpx.AsyncClient, args: argparse.Namespace) -> int:  # noqa: ARG001
    """Bundle the package into a single JSON file, optionally signed (fase 19.3)."""
    from app.services import custom_node_service

    manifest, code = _read_package(args.directory)
    manifest = custom_node_service.validate_manifest(manifest, code)
    package: dict = {"manifest": manifest, "code": code}
    if args.sign_key:
        package["signature"] = custom_node_service.sign_package(manifest, code, args.sign_key)
    out = args.out or os.path.join(args.directory, "package.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(package, fh, indent=2, default=str)
    print(f"wrote {out}")
    return 0


async def cmd_node_push(client: httpx.AsyncClient, args: argparse.Namespace) -> int:
    """Upload a packaged custom node to the server's install endpoint."""
    with open(args.package, encoding="utf-8") as fh:
        package = json.load(fh)
    resp = await client.post("/graph-workflows/custom-nodes", json=package)
    return _print(resp)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sibyl-wf", description="SpiceSibyl graph-workflow CLI")
    parser.add_argument("--api-url", default=os.environ.get("SIBYL_API_URL", _DEFAULT_API_URL))
    parser.add_argument("--api-key", default=os.environ.get("SIBYL_API_KEY"))
    parser.add_argument("--profile-id", default=os.environ.get("SIBYL_PROFILE_ID"))
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run a workflow now")
    p_run.add_argument("workflow_id")
    p_run.add_argument("--trigger", help="Path to a JSON file used as the $trigger payload")
    p_run.set_defaults(func=cmd_run)

    p_export = sub.add_parser("export", help="Export a workflow's portable JSON snapshot")
    p_export.add_argument("workflow_id")
    p_export.add_argument("--out", help="Write to this file instead of stdout")
    p_export.set_defaults(func=cmd_export)

    p_import = sub.add_parser("import", help="Create a workflow from an exported JSON snapshot")
    p_import.add_argument("file")
    p_import.set_defaults(func=cmd_import)

    p_test = sub.add_parser("test", help="Test a single node in isolation (fase 3.1)")
    p_test.add_argument("workflow_id")
    p_test.add_argument("node_id")
    p_test.add_argument("--input", help="Path to a JSON file used to override the node's $json input")
    p_test.set_defaults(func=cmd_test)

    p_logs = sub.add_parser("logs", help="Show a run's status and per-node results")
    p_logs.add_argument("run_id")
    p_logs.set_defaults(func=cmd_logs)

    # Custom Node SDK authoring (fase 19.4): sibyl-wf node init|test|pack|push
    p_node = sub.add_parser("node", help="Author and publish custom nodes (fase 19)")
    node_sub = p_node.add_subparsers(dest="node_command", required=True)

    p_ni = node_sub.add_parser("init", help="Scaffold a custom node package")
    p_ni.add_argument("directory")
    p_ni.add_argument("--type", default="custom.example", help="Node type (custom.<name>)")
    p_ni.add_argument("--kind", choices=["declarative", "python"], default="declarative")
    p_ni.set_defaults(func=cmd_node_init)

    p_nt = node_sub.add_parser("test", help="Validate a package locally (fase 3.1)")
    p_nt.add_argument("directory")
    p_nt.set_defaults(func=cmd_node_test)

    p_np = node_sub.add_parser("pack", help="Bundle a package into a JSON file (optionally signed)")
    p_np.add_argument("directory")
    p_np.add_argument("--out", help="Output path (default <dir>/package.json)")
    p_np.add_argument("--sign-key", help="Sign the package with this HMAC key")
    p_np.set_defaults(func=cmd_node_pack)

    p_npush = node_sub.add_parser("push", help="Upload a packaged custom node to the server")
    p_npush.add_argument("package")
    p_npush.set_defaults(func=cmd_node_push)

    return parser


async def _async_main(argv: list[str] | None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Local-only authoring commands (init/test/pack) never hit the server.
    _local = {cmd_node_init, cmd_node_test, cmd_node_pack}
    if args.func not in _local and not args.api_key:
        parser.error("--api-key or SIBYL_API_KEY is required")
    async with _client(args.api_url, args.api_key, args.profile_id) as client:
        try:
            return await args.func(client, args)
        except httpx.HTTPError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
