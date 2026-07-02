"""
Phase 18 — persistent multi-step workflow (agent run) endpoints.

Routes (under /v1/workflows):
  POST   /                — create a run and start it in the background
  GET    /                — list the profile's runs (most recent first)
  GET    /{id}            — one run with its full step trace
  POST   /{id}/pause      — stop at the next iteration boundary (resumable)
  POST   /{id}/resume     — continue a paused run from its checkpoint
  POST   /{id}/cancel     — terminally stop a run
  DELETE /{id}            — remove a finished run and its steps
"""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import settings
from app.db import audit_repository, workflow_repository
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.schemas.auth import UserOut
from app.schemas.workflows import AgentRunCreate, AgentRunOut
from app.services import workflow_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _owned_run(
    db: aiosqlite.Connection, run_id: str, profile_id: str
) -> AgentRunOut:
    run = await workflow_repository.get_run(db, run_id)
    if not run or run.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return run


@router.post("", response_model=AgentRunOut, status_code=201)
async def create_run(
    body: AgentRunCreate,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    max_steps = min(
        body.max_steps or settings.workflow_default_max_steps,
        settings.workflow_max_steps_limit,
    )
    run = await workflow_repository.create_run(db, profile_id, body.goal, body.model, max_steps)
    workflow_service.start(run.id, system_prompt=body.system_prompt)
    await audit_repository.record(
        db, user.id, "workflow.create", resource=run.id, ip=_client_ip(request)
    )
    return run


@router.get("", response_model=list[AgentRunOut])
async def list_runs(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    runs = await workflow_repository.list_runs(db, profile_id)
    return [await workflow_service.reconcile(db, r) for r in runs]


@router.get("/{run_id}", response_model=AgentRunOut)
async def get_run(
    run_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    run = await _owned_run(db, run_id, profile_id)
    run = await workflow_service.reconcile(db, run)
    run.steps = await workflow_repository.list_steps(db, run_id)
    return run


@router.post("/{run_id}/pause", response_model=AgentRunOut)
async def pause_run(
    run_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    await _owned_run(db, run_id, profile_id)
    if not await workflow_service.pause(db, run_id):
        raise HTTPException(status_code=409, detail="Run is not running")
    await audit_repository.record(
        db, user.id, "workflow.pause", resource=run_id, ip=_client_ip(request)
    )
    return await _owned_run(db, run_id, profile_id)


@router.post("/{run_id}/resume", response_model=AgentRunOut)
async def resume_run(
    run_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    run = await _owned_run(db, run_id, profile_id)
    run = await workflow_service.reconcile(db, run)
    if not await workflow_service.resume(db, run_id):
        raise HTTPException(status_code=409, detail="Run is not paused")
    await audit_repository.record(
        db, user.id, "workflow.resume", resource=run_id, ip=_client_ip(request)
    )
    return await _owned_run(db, run_id, profile_id)


@router.post("/{run_id}/cancel", response_model=AgentRunOut)
async def cancel_run(
    run_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    await _owned_run(db, run_id, profile_id)
    if not await workflow_service.cancel(db, run_id):
        raise HTTPException(status_code=409, detail="Run already finished")
    await audit_repository.record(
        db, user.id, "workflow.cancel", resource=run_id, ip=_client_ip(request)
    )
    return await _owned_run(db, run_id, profile_id)


@router.delete("/{run_id}", status_code=204)
async def delete_run(
    run_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    run = await _owned_run(db, run_id, profile_id)
    if run.status in ("pending", "running"):
        await workflow_service.cancel(db, run_id)
    await workflow_repository.delete_run(db, run_id)
    await audit_repository.record(
        db, user.id, "workflow.delete", resource=run_id, ip=_client_ip(request)
    )
