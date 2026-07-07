"""
Phase 18 — MCP manager service.

Bridges the persisted ``mcp_servers`` registry (DB) with the live stdio runtime
(``mcp_client``). Responsibilities:

* discover the tools each enabled server exposes (with health/error capture);
* expose those tools to the chat tool-loop as OpenAI-format function defs,
  namespaced ``mcp__<server>__<tool>`` so they never collide with built-ins;
* route a namespaced tool call back to the owning server and invoke it;
* import/export the standard ``{"mcpServers": {...}}`` bundle.

A small in-memory routing cache (namespaced tool → server config + raw name) is
rebuilt by :func:`refresh`, called whenever the tool list is fetched and after
any registry mutation, so :func:`call_tool` stays DB-free on the hot path.
"""

import asyncio
import json
import logging
import os
import re
import shutil

import aiosqlite

from app.db import mcp_repository
from app.schemas.mcp import (
    McpConfigBundle,
    McpDeploymentCheckItem,
    McpRuntimeReport,
    McpServerConfig,
    McpServerOut,
    McpToolInfo,
)
from app.services import mcp_client

logger = logging.getLogger(__name__)

_TOOL_PREFIX = "mcp"
_SEP = "__"
_INVALID = re.compile(r"[^a-zA-Z0-9_-]")

# Routing cache: namespaced tool name -> (server config, raw tool name).
_routes: dict[str, tuple[McpServerConfig, str]] = {}
# Cache of OpenAI tool defs built at the last refresh.
_tool_defs: list[dict] = []
_lock = asyncio.Lock()


def _sanitize(part: str) -> str:
    return _INVALID.sub("_", part)


def namespaced(server_name: str, tool_name: str) -> str:
    return f"{_TOOL_PREFIX}{_SEP}{_sanitize(server_name)}{_SEP}{_sanitize(tool_name)}"


def is_mcp_tool(name: str) -> bool:
    return name.startswith(f"{_TOOL_PREFIX}{_SEP}")


def _tool_def(server_name: str, tool: McpToolInfo) -> dict:
    schema = tool.input_schema or {"type": "object", "properties": {}}
    desc = tool.description or f"Tool '{tool.name}' from MCP server '{server_name}'."
    return {
        "type": "function",
        "function": {
            "name": namespaced(server_name, tool.name),
            "description": f"[{server_name}] {desc}",
            "parameters": schema,
        },
    }


async def _probe(server: McpServerOut) -> dict:
    """Connect to one server and list its tools. Returns a UI state dict."""
    try:
        async with mcp_client.open_session(server.config) as session:
            tools = await session.list_tools()
        return {"status": "ok", "error": None, "tools": tools}
    except mcp_client.MCPError as exc:
        logger.warning("MCP probe failed for '%s': %s", server.name, exc)
        return {"status": "error", "error": str(exc), "tools": []}
    except Exception as exc:  # noqa: BLE001 — a bad server must not crash the probe sweep
        logger.exception("MCP probe crashed for '%s'", server.name)
        return {"status": "error", "error": str(exc), "tools": []}


async def refresh(db: aiosqlite.Connection) -> list[McpServerOut]:
    """Re-probe every enabled server and rebuild the routing cache + tool defs.

    Returns the full server list with live ``status``/``tools`` populated
    (disabled servers are returned untouched, not probed).
    """
    servers = await mcp_repository.list_servers(db)
    enabled = [s for s in servers if s.enabled]

    results = await asyncio.gather(*(_probe(s) for s in enabled)) if enabled else []
    state_by_name = {s.name: r for s, r in zip(enabled, results)}

    routes: dict[str, tuple[McpServerConfig, str]] = {}
    defs: list[dict] = []
    for server in enabled:
        state = state_by_name[server.name]
        for tool in state["tools"]:
            routes[namespaced(server.name, tool.name)] = (server.config, tool.name)
            defs.append(_tool_def(server.name, tool))

    async with _lock:
        _routes.clear()
        _routes.update(routes)
        _tool_defs.clear()
        _tool_defs.extend(defs)

    # Fold live state back into the returned objects for the UI.
    for server in servers:
        if server.name in state_by_name:
            st = state_by_name[server.name]
            server.status = st["status"]
            server.error = st["error"]
            server.tools = st["tools"]
    return servers


def get_tool_definitions() -> list[dict]:
    """OpenAI-format tool defs for all discovered MCP tools (from last refresh)."""
    return list(_tool_defs)


async def call_tool(name: str, arguments: dict) -> str:
    """Route a namespaced ``mcp__server__tool`` call to its server and invoke it."""
    from app.core.config import settings

    route = _routes.get(name)
    if route is None:
        logger.warning("MCP call to unknown/unavailable tool '%s'", name)
        return (
            f"Unknown or unavailable MCP tool '{name}'. "
            "The server may be disabled or unreachable."
        )
    config, raw_name = route
    if settings.mcp_log_calls:
        target = config.url if config.transport == "sse" else config.command
        logger.info(
            "MCP call → %s (%s: %s) args=%s",
            name, config.transport, target, json.dumps(arguments or {}, ensure_ascii=False),
        )
    try:
        async with mcp_client.open_session(config) as session:
            result = await session.call_tool(raw_name, arguments or {})
    except mcp_client.MCPError as exc:
        logger.warning("MCP call '%s' failed: %s", name, exc)
        return f"MCP tool '{name}' failed: {exc}"
    if settings.mcp_log_calls:
        logger.info(
            "MCP result ← %s: %s",
            name, mcp_client._truncate(result, settings.mcp_log_max_chars),
        )
    return result


async def probe_one(server: McpServerOut) -> McpServerOut:
    """Probe a single server (for the 'test' endpoint), folding state into it."""
    if not server.enabled:
        server.status = "disabled"
        return server
    state = await _probe(server)
    server.status = state["status"]
    server.error = state["error"]
    server.tools = state["tools"]
    return server


# ── Import / export (standard mcpServers bundle) ──────────────────────────────
async def export_bundle(db: aiosqlite.Connection) -> McpConfigBundle:
    servers = await mcp_repository.list_servers(db)
    return McpConfigBundle(mcpServers={s.name: s.config for s in servers})


async def import_bundle(
    db: aiosqlite.Connection, bundle: McpConfigBundle, enabled: bool = True
) -> list[McpServerOut]:
    """Upsert every server in a standard bundle. Returns the imported servers."""
    out: list[McpServerOut] = []
    for name, config in bundle.mcpServers.items():
        out.append(await mcp_repository.upsert_server(db, name, config, enabled))
    return out


# ── Runtime detection & deployment calculator (Phase 23.5) ────────────────────
def runtime_report() -> McpRuntimeReport:
    """What the running backend image can launch stdio servers with."""
    from app.core.config import settings

    return McpRuntimeReport(
        stdio_enabled=settings.mcp_stdio_enabled,
        allowed_commands=sorted(mcp_client._allowed_commands()),
        runtimes=mcp_client.detect_runtimes(),
    )


def deployment_check(bundle: McpConfigBundle) -> list[McpDeploymentCheckItem]:
    """For each server in a pasted bundle, say what this deployment needs.

    Backs the ``/mcp`` config calculator (Phase 23.5.d): ``docker run …`` needs
    only the already-mounted socket, ``npx``/``uvx`` need the Node/uv runtime
    layers (23.5.a), and a remote ``url`` works unchanged.
    """
    report = runtime_report()
    items: list[McpDeploymentCheckItem] = []
    for name, config in bundle.mcpServers.items():
        if config.transport == "sse":
            items.append(
                McpDeploymentCheckItem(
                    name=name, transport="sse", requirement="remote url",
                    ok=True, detail="Works today over HTTP+SSE — no host changes needed.",
                )
            )
            continue

        command = (config.command or "").strip()
        base = os.path.basename(command)
        allowed = set(report.allowed_commands)
        if not report.stdio_enabled:
            ok, detail, requirement = False, "stdio transport is disabled (MCP_STDIO_ENABLED=false).", base
        elif not mcp_client._command_allowed(base, allowed):
            ok, detail, requirement = (
                False,
                f"'{base}' is not in MCP_ALLOWED_COMMANDS ({', '.join(sorted(allowed))}).",
                base,
            )
        elif base == "docker":
            requirement, ok, detail = "docker socket", True, "Docker socket mount present ✅"
        elif base in ("npx", "node"):
            requirement = "Node runtime"
            ok = report.runtimes.get("node", False) and (base != "npx" or report.runtimes.get("npx", False))
            detail = "Node runtime available ✅" if ok else "Requires the Node runtime layer (Phase 23.5.a)."
        elif base in ("uv", "uvx"):
            requirement = "uv runtime"
            ok = report.runtimes.get(base, False)
            detail = "uv runtime available ✅" if ok else "Requires the uv/uvx runtime layer (Phase 23.5.a)."
        elif base.startswith("python"):
            requirement, ok = "Python runtime", report.runtimes.get("python", False)
            detail = "Python runtime available ✅" if ok else "Python not found in the backend image."
        else:
            requirement = base or "command"
            ok = bool(command) and shutil.which(command) is not None
            detail = "Found on PATH ✅" if ok else "Not found on PATH — verify it's installed in the backend image."

        items.append(
            McpDeploymentCheckItem(
                name=name, transport="stdio", command=command,
                requirement=requirement, ok=ok, detail=detail,
            )
        )
    return items
