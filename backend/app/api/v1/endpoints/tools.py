"""
Tools endpoints — built-in + MCP + user-defined custom tools.

Phase 18 adds per-profile custom tools (HTTP-backed, registered from the UI):

Routes (under /v1/tools):
  GET    /                    — every tool available to the caller's profile
  GET    /custom              — list the profile's custom tools
  POST   /custom              — create/replace a custom tool (by name)
  PATCH  /custom/{id}         — enable/disable
  DELETE /custom/{id}         — remove a tool
  POST   /custom/{id}/test    — invoke the tool once with sample arguments
"""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.db import audit_repository, custom_tool_repository
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.schemas.auth import UserOut
from app.schemas.custom_tools import (
    CustomToolIn,
    CustomToolOut,
    CustomToolTestRequest,
    CustomToolTestResult,
)
from app.services import custom_tool_service, mcp_service
from app.tools.registry import TOOL_DEFINITIONS

logger = logging.getLogger(__name__)

router = APIRouter()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("")
async def list_tools(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Built-in tools, every tool discovered from enabled MCP servers, and the
    caller profile's enabled custom tools.

    Refreshing here (re-probing enabled servers) keeps the routing cache the chat
    tool-loop relies on warm: the frontend fetches this before enabling tools.
    Discovery failures are swallowed so a broken MCP server never hides the
    built-in tools.
    """
    tools = list(TOOL_DEFINITIONS)
    try:
        await mcp_service.refresh(db)
        tools.extend(mcp_service.get_tool_definitions())
    except Exception:  # noqa: BLE001 — MCP is optional; never break the tool list
        logger.exception("MCP tool discovery failed; returning built-in tools only")
    try:
        tools.extend(await custom_tool_service.get_tool_definitions(db, profile_id))
    except Exception:  # noqa: BLE001 — custom tools must never hide the built-ins
        logger.exception("Custom tool listing failed; returning without them")
    return tools


# ── Phase 18: user-defined custom tools ──────────────────────────────────────
@router.get("/custom", response_model=list[CustomToolOut])
async def list_custom_tools(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    return await custom_tool_repository.list_tools(db, profile_id)


@router.post("/custom", response_model=CustomToolOut, status_code=201)
async def create_custom_tool(
    body: CustomToolIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    tool = await custom_tool_repository.upsert_tool(db, profile_id, body)
    await audit_repository.record(
        db, user.id, "custom_tool.upsert", resource=body.name, ip=_client_ip(request)
    )
    return tool


@router.patch("/custom/{tool_id}", response_model=CustomToolOut)
async def toggle_custom_tool(
    tool_id: str,
    body: dict,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    if "enabled" not in (body or {}):
        raise HTTPException(status_code=422, detail="Missing 'enabled'")
    existing = await custom_tool_repository.get_tool(db, tool_id)
    if not existing or existing.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Custom tool not found")
    tool = await custom_tool_repository.set_enabled(db, tool_id, bool(body["enabled"]))
    await audit_repository.record(
        db, user.id,
        "custom_tool.enable" if tool.enabled else "custom_tool.disable",  # type: ignore[union-attr]
        resource=existing.name, ip=_client_ip(request),
    )
    return tool


@router.delete("/custom/{tool_id}", status_code=204)
async def delete_custom_tool(
    tool_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    existing = await custom_tool_repository.get_tool(db, tool_id)
    if not existing or existing.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Custom tool not found")
    await custom_tool_repository.delete_tool(db, tool_id)
    await audit_repository.record(
        db, user.id, "custom_tool.delete", resource=existing.name, ip=_client_ip(request)
    )


@router.post("/custom/{tool_id}/test", response_model=CustomToolTestResult)
async def test_custom_tool(
    tool_id: str,
    body: CustomToolTestRequest,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    tool = await custom_tool_repository.get_tool(db, tool_id)
    if not tool or tool.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Custom tool not found")
    result = await custom_tool_service.invoke(tool, body.arguments)
    ok = not result.startswith((f"Custom tool '{tool.name}' failed",
                                f"Custom tool '{tool.name}' returned HTTP"))
    return CustomToolTestResult(ok=ok, result=result)
