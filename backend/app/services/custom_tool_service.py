"""
Phase 18 — user-defined custom tools service.

Bridges the ``custom_tools`` registry (DB) with the chat tool loop:

* expose enabled tools as OpenAI-format function defs, namespaced
  ``custom__<name>`` so they never collide with built-ins or MCP tools;
* route a namespaced call to its stored HTTP endpoint and invoke it —
  arguments go as the JSON body (POST/PUT/PATCH) or query params (GET),
  the response body comes back as the tool result string.

Tools are per profile, so execution resolves the tool by (profile, name) at
call time — no in-memory routing cache to keep coherent across profiles.
"""

import json
import logging

import aiosqlite
import httpx

from app.core.config import settings
from app.db import custom_tool_repository
from app.schemas.custom_tools import CustomToolOut

logger = logging.getLogger(__name__)

_TOOL_PREFIX = "custom"
_SEP = "__"

_MAX_RESULT_CHARS = 8000


def namespaced(tool_name: str) -> str:
    return f"{_TOOL_PREFIX}{_SEP}{tool_name}"


def is_custom_tool(name: str) -> bool:
    return name.startswith(f"{_TOOL_PREFIX}{_SEP}")


def _raw_name(name: str) -> str:
    return name[len(_TOOL_PREFIX) + len(_SEP):]


def tool_def(tool: CustomToolOut) -> dict:
    desc = tool.description or f"User-defined tool '{tool.name}'."
    return {
        "type": "function",
        "function": {
            "name": namespaced(tool.name),
            "description": desc,
            "parameters": tool.parameters or {"type": "object", "properties": {}},
        },
    }


async def get_tool_definitions(db: aiosqlite.Connection, profile_id: str) -> list[dict]:
    """OpenAI-format defs for every enabled custom tool of the profile."""
    tools = await custom_tool_repository.list_tools(db, profile_id, enabled_only=True)
    return [tool_def(t) for t in tools]


async def invoke(tool: CustomToolOut, arguments: dict) -> str:
    """Execute one custom tool call against its HTTP endpoint."""
    ep = tool.endpoint
    headers = dict(ep.headers or {})
    if ep.auth.type == "bearer" and ep.auth.token:
        headers["Authorization"] = f"Bearer {ep.auth.token}"
    elif ep.auth.type == "header" and ep.auth.name:
        headers[ep.auth.name] = ep.auth.value or ""

    try:
        async with httpx.AsyncClient(timeout=ep.timeout, follow_redirects=True) as client:
            if ep.method == "GET":
                # Flatten arguments into query params (JSON-encode nested values).
                params = {
                    k: v if isinstance(v, (str, int, float, bool)) else json.dumps(v)
                    for k, v in (arguments or {}).items()
                }
                resp = await client.get(ep.url, params=params, headers=headers)
            else:
                resp = await client.request(
                    ep.method, ep.url, json=arguments or {}, headers=headers
                )
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("Custom tool '%s' request failed: %s", tool.name, exc)
        return f"Custom tool '{tool.name}' failed: {exc}"

    body = resp.text or ""
    if len(body) > _MAX_RESULT_CHARS:
        body = body[:_MAX_RESULT_CHARS] + f"\n[Truncated — {len(body) - _MAX_RESULT_CHARS} chars omitted]"
    if resp.status_code >= 400:
        return f"Custom tool '{tool.name}' returned HTTP {resp.status_code}: {body}"
    return body or f"Custom tool '{tool.name}' returned an empty response (HTTP {resp.status_code})."


async def call_tool(name: str, arguments: dict, profile_id: str) -> str:
    """Route a namespaced ``custom__<tool>`` call: resolve by profile and invoke."""
    raw = _raw_name(name)
    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    try:
        tool = await custom_tool_repository.get_by_name(db, profile_id, raw)
    finally:
        await db.close()
    if tool is None or not tool.enabled:
        logger.warning("Custom tool call to unknown/disabled tool '%s' (profile=%s)", raw, profile_id)
        return f"Unknown or disabled custom tool '{raw}'."
    logger.info(
        "Custom tool call → %s (%s %s) profile=%s",
        raw, tool.endpoint.method, tool.endpoint.url, profile_id,
    )
    return await invoke(tool, arguments or {})
