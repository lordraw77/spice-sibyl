"""
Admin ops endpoints (Phase 16) — DB backup / restore and per-profile
export / import.  All routes require the ``admin`` role and are recorded in the
audit log.
"""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, File

from app.db import audit_repository, profile_repository
from app.db.database import get_db
from app.dependencies.auth import require_role
from app.schemas.auth import UserOut
from app.services import backup_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/backup")
async def create_backup(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    path = await backup_service.make_backup()
    backup_service.prune()
    await audit_repository.record(
        db, admin.id, "admin.backup", resource=path.name, ip=_client_ip(request)
    )
    return {"name": path.name}


@router.get("/backups")
async def list_backups(admin: UserOut = Depends(require_role("admin"))):
    return {"backups": backup_service.list_backups()}


@router.post("/restore")
async def restore_backup(
    request: Request,
    body: dict,
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    name = (body or {}).get("name")
    if not name:
        raise HTTPException(status_code=422, detail="Missing 'name'")
    try:
        await backup_service.restore_backup(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    await audit_repository.record(
        db, admin.id, "admin.restore", resource=name, ip=_client_ip(request)
    )
    return {"status": "restored", "name": name, "note": "Restart the service to reload connections."}


async def _require_owned_profile(db: aiosqlite.Connection, profile_id: str, user: UserOut) -> None:
    profile = await profile_repository.get_profile(db, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    # Admins may export any profile, but the profile must exist.


@router.get("/export")
async def export_profile(
    request: Request,
    profile_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    await _require_owned_profile(db, profile_id, admin)
    archive = await backup_service.export_profile(profile_id)
    await audit_repository.record(
        db, admin.id, "admin.export", resource=profile_id, ip=_client_ip(request)
    )
    return Response(
        content=archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="profile-{profile_id}.zip"'},
    )


@router.post("/import")
async def import_profile(
    request: Request,
    profile_id: str,
    file: UploadFile = File(...),
    db: aiosqlite.Connection = Depends(get_db),
    admin: UserOut = Depends(require_role("admin")),
):
    await _require_owned_profile(db, profile_id, admin)
    archive = await file.read()
    try:
        counts = await backup_service.import_profile(archive, profile_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid archive: {exc}")
    await audit_repository.record(
        db, admin.id, "admin.import", resource=profile_id, ip=_client_ip(request)
    )
    return {"status": "imported", "profile_id": profile_id, "counts": counts}
