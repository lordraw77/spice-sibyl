"""
Phase 18 — MCP server registry endpoints.

Admin-only CRUD over MCP servers stored in the standard ``mcpServers`` config
shape, plus live health/tool discovery and standard-format import/export. All
mutations are recorded in the audit log.

Routes (under /v1/mcp):
  GET    /servers              — list servers (live status when ?probe=true)
  POST   /servers              — create/replace a server (by name)
  GET    /servers/{id}         — one server with live probe state
  PATCH  /servers/{id}         — enable/disable
  DELETE /servers/{id}         — remove a server
  POST   /servers/{id}/test    — probe one server (handshake + tools/list)
  POST   /reload               — re-probe all enabled servers, rebuild tool cache
  GET    /config               — export the {"mcpServers": {...}} bundle
  POST   /import               — import a {"mcpServers": {...}} bundle
"""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.db import audit_repository, mcp_repository
from app.db.database import get_db
from app.dependencies.auth import require_role
from app.schemas.auth import UserOut
from app.schemas.mcp import McpConfigBundle, McpServerIn, McpServerOut
from app.services import mcp_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/servers", response_model=list[McpServerOut])
async def list_servers(
    probe: bool = False,
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    """List registered MCP servers. With ``?probe=true`` connect to each enabled
    server to populate live status + discovered tools (and refresh the tool cache)."""
    if probe:
        return await mcp_service.refresh(db)
    return await mcp_repository.list_servers(db)


@router.post("/servers", response_model=McpServerOut, status_code=201)
async def create_server(
    body: McpServerIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    server = await mcp_repository.upsert_server(db, body.name, body.config, body.enabled)
    await mcp_service.refresh(db)
    await audit_repository.record(
        db, admin.id, "mcp.upsert", resource=body.name, ip=_client_ip(request)
    )
    return await mcp_service.probe_one(server)


@router.get("/servers/{server_id}", response_model=McpServerOut)
async def get_server(
    server_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    server = await mcp_repository.get_server(db, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return await mcp_service.probe_one(server)


@router.patch("/servers/{server_id}", response_model=McpServerOut)
async def toggle_server(
    server_id: str,
    body: dict,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    if "enabled" not in (body or {}):
        raise HTTPException(status_code=422, detail="Missing 'enabled'")
    server = await mcp_repository.set_enabled(db, server_id, bool(body["enabled"]))
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    await mcp_service.refresh(db)
    await audit_repository.record(
        db, admin.id,
        "mcp.enable" if server.enabled else "mcp.disable",
        resource=server.name, ip=_client_ip(request),
    )
    return await mcp_service.probe_one(server)


@router.delete("/servers/{server_id}", status_code=204)
async def delete_server(
    server_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    server = await mcp_repository.get_server(db, server_id)
    if not await mcp_repository.delete_server(db, server_id):
        raise HTTPException(status_code=404, detail="MCP server not found")
    await mcp_service.refresh(db)
    await audit_repository.record(
        db, admin.id, "mcp.delete",
        resource=server.name if server else server_id, ip=_client_ip(request),
    )


@router.post("/servers/{server_id}/test", response_model=McpServerOut)
async def test_server(
    server_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    server = await mcp_repository.get_server(db, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return await mcp_service.probe_one(server)


@router.post("/reload", response_model=list[McpServerOut])
async def reload_servers(
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    return await mcp_service.refresh(db)


@router.get("/config", response_model=McpConfigBundle)
async def export_config(
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    return await mcp_service.export_bundle(db)


@router.post("/import", response_model=list[McpServerOut])
async def import_config(
    bundle: McpConfigBundle,
    request: Request,
    enabled: bool = True,
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    if not bundle.mcpServers:
        raise HTTPException(status_code=422, detail="'mcpServers' is empty")
    imported = await mcp_service.import_bundle(db, bundle, enabled=enabled)
    await mcp_service.refresh(db)
    await audit_repository.record(
        db, admin.id, "mcp.import",
        resource=",".join(s.name for s in imported), ip=_client_ip(request),
    )
    return imported
