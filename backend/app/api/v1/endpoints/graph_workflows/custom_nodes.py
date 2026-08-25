"""Custom Node SDK registry (Phase 51 / roadmap fase 19).

Extracted verbatim from the former single-file graph_workflows.py.
"""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.db import audit_repository, graph_workflow_repository as repo
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.schemas.auth import UserOut
from app.schemas.graph_workflows import CustomNodeEnableIn, CustomNodeInstallIn, CustomNodeOut
from app.services import custom_node_service

from ._common import _client_ip

router = APIRouter()

# ── custom nodes (Phase 51 / roadmap fase 19) ────────────────────────────────

def _custom_node_out(node: dict, *, with_code: bool = False) -> CustomNodeOut:
    return CustomNodeOut(
        id=node["id"], type=node["type"], version=node["version"], name=node["name"],
        description=node["description"], category=node["category"], icon=node["icon"],
        kind=node["kind"], manifest=node["manifest"],
        code=node["code"] if with_code else None,
        enabled=node["enabled"], created_at=node["created_at"], updated_at=node["updated_at"],
    )


@router.get("/custom-nodes", response_model=list[CustomNodeOut])
async def list_custom_nodes(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """The current version of every custom node the profile has installed
    (fase 19.2). Static path declared before ``/{wf_id}`` so it isn't swallowed."""
    return [_custom_node_out(n) for n in await repo.list_custom_nodes(db, profile_id)]


@router.post("/custom-nodes", response_model=CustomNodeOut, status_code=201)
async def install_custom_node(
    body: CustomNodeInstallIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Install (or upgrade) a custom node from a manifest + optional code
    (fase 19.1/19.3). A python node's code is always run in the sandbox; a
    validation failure is a 400. Install is audited (fase 7.3)."""
    try:
        node = await custom_node_service.install(db, profile_id, body.manifest, body.code, body.signature)
    except custom_node_service.CustomNodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_repository.record(
        db, user.id, "custom_node.install", resource=node["type"], ip=_client_ip(request),
        detail=f"v{node['version']} ({node['kind']})",
    )
    return _custom_node_out(node, with_code=True)


@router.get("/custom-nodes/{node_type}", response_model=CustomNodeOut)
async def get_custom_node(
    node_type: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    node = await repo.get_custom_node(db, profile_id, node_type)
    if node is None:
        raise HTTPException(status_code=404, detail="Custom node not found")
    return _custom_node_out(node, with_code=True)


@router.get("/custom-nodes/{node_type}/versions", response_model=list[CustomNodeOut])
async def list_custom_node_versions(
    node_type: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    return [_custom_node_out(n) for n in await repo.list_custom_node_versions(db, profile_id, node_type)]


@router.post("/custom-nodes/{node_type}/versions", response_model=CustomNodeOut, status_code=201)
async def add_custom_node_version(
    node_type: str,
    body: CustomNodeInstallIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Upload a new version of an existing custom node type (fase 19.2). The
    manifest's ``type`` must match the path; older versions keep running."""
    if str(body.manifest.get("type") or "") != node_type:
        raise HTTPException(status_code=400, detail="manifest.type must match the path")
    try:
        node = await custom_node_service.install(db, profile_id, body.manifest, body.code, body.signature)
    except custom_node_service.CustomNodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_repository.record(
        db, user.id, "custom_node.version", resource=node["type"], ip=_client_ip(request),
        detail=f"v{node['version']}",
    )
    return _custom_node_out(node, with_code=True)


@router.patch("/custom-nodes/{node_type}", response_model=CustomNodeOut)
async def set_custom_node_enabled(
    node_type: str,
    body: CustomNodeEnableIn,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),  # noqa: ARG001 — enforce auth
):
    if not await repo.set_custom_node_enabled(db, profile_id, node_type, body.enabled):
        raise HTTPException(status_code=404, detail="Custom node not found")
    node = await repo.get_custom_node(db, profile_id, node_type)
    return _custom_node_out(node)


@router.delete("/custom-nodes/{node_type}", status_code=204)
async def delete_custom_node(
    node_type: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Delete every version of a custom node type. Blocked with a 409 (and the
    list of dependents) while any workflow still references it (fase 19.2)."""
    if await repo.get_custom_node(db, profile_id, node_type) is None:
        raise HTTPException(status_code=404, detail="Custom node not found")
    dependents = await custom_node_service.delete(db, profile_id, node_type)
    if dependents:
        raise HTTPException(
            status_code=409,
            detail={"message": "Custom node is still used by workflows", "dependents": dependents},
        )
    await audit_repository.record(
        db, user.id, "custom_node.delete", resource=node_type, ip=_client_ip(request)
    )
    return Response(status_code=204)
