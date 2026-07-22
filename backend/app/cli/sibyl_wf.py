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

    return parser


async def _async_main(argv: list[str] | None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.api_key:
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
