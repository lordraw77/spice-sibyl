"""Remote runners (Phase 46 / roadmap fase 14.1): registration plus the
public outbound-only runner protocol.

Extracted verbatim from the former single-file graph_workflows.py.
"""

import asyncio
import json
import time

import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from app.core.config import settings
from app.db import audit_repository, graph_workflow_repository as repo
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.schemas.auth import UserOut
from app.schemas.graph_workflows import (
    RunnerHeartbeatIn,
    RunnerJobOut,
    RunnerJobResultIn,
    RunnerOut,
    RunnerRegisterIn,
    RunnerRegisterOut,
)

from ._common import _client_ip

router = APIRouter()
public_router = APIRouter()

# ── remote runners (Phase 46 — roadmap fase 14.1) ───────────────────────────
# Registered ahead of GET/{wf_id} below so "/runners" never matches as a
# workflow id.

@router.post("/runners", response_model=RunnerRegisterOut, status_code=201)
async def register_runner(
    body: RunnerRegisterIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Provision a new remote-runner slot. The raw token is returned ONLY here
    — start the agent process with it (e.g. ``SIBYL_RUNNER_TOKEN=... python -m
    app.runner.agent``); it is never retrievable again (only its hash is kept)."""
    runner_id, token = await repo.create_runner(
        db, profile_id, body.name, body.labels, body.allowed_node_types,
    )
    await audit_repository.record(
        db, user.id, "graph_workflow.runner.register", resource=runner_id, ip=_client_ip(request)
    )
    return RunnerRegisterOut(id=runner_id, token=token)


@router.get("/runners", response_model=list[RunnerOut])
async def list_runners(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    return await repo.list_runners(
        db, profile_id, heartbeat_timeout=settings.graph_workflow_runner_heartbeat_timeout,
    )


@router.delete("/runners/{runner_id}", status_code=204)
async def revoke_runner(
    runner_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    row = await repo.get_runner_row(db, runner_id)
    if row is None or row["profile_id"] != profile_id:
        raise HTTPException(status_code=404, detail="Runner not found")
    await repo.revoke_runner(db, runner_id)
    await audit_repository.record(
        db, user.id, "graph_workflow.runner.revoke", resource=runner_id, ip=_client_ip(request)
    )


# ── public remote-runner protocol (Phase 46 — roadmap fase 14.1) ───────────
# Outbound-only from the runner's point of view: it calls these, the backend
# never calls it. Authenticated by X-Runner-Token (a per-runner secret, never
# the user's session) instead of the usual bearer/profile dependencies.

async def _runner_auth(
    x_runner_token: str | None, db: aiosqlite.Connection,
) -> aiosqlite.Row:
    if not x_runner_token:
        raise HTTPException(status_code=401, detail="Missing X-Runner-Token")
    runner = await repo.get_runner_by_token(db, x_runner_token)
    if runner is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked runner token")
    return runner


@public_router.post("/runners/heartbeat")
async def runner_heartbeat(
    body: RunnerHeartbeatIn,
    x_runner_token: str | None = Header(default=None),
    db: aiosqlite.Connection = Depends(get_db),
):
    runner = await _runner_auth(x_runner_token, db)
    await repo.heartbeat_runner(db, runner["id"], version=body.version, labels=body.labels)
    return {"ok": True}


@public_router.get("/runners/jobs/next")
async def poll_runner_job(
    wait: float = 20.0,
    x_runner_token: str | None = Header(default=None),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Long-poll for the next job assigned to this runner: claims and returns
    it (the fase 3.1 test_node() request shape) as soon as one is queued, else
    204 once ``wait`` seconds (capped) pass with nothing to do."""
    runner = await _runner_auth(x_runner_token, db)
    deadline = time.time() + min(max(wait, 0.0), 55.0)
    while True:
        row = await repo.claim_next_runner_job(db, runner["id"])
        if row is not None:
            payload = json.loads(row["payload_json"])
            return RunnerJobOut(
                job_id=row["id"], node_id=row["node_id"], node_type=row["node_type"],
                params=payload.get("params") or {}, input=payload.get("input"),
            )
        if time.time() >= deadline:
            return Response(status_code=204)
        await asyncio.sleep(0.5)


@public_router.post("/runners/jobs/{job_id}/result")
async def submit_runner_job_result(
    job_id: str,
    body: RunnerJobResultIn,
    x_runner_token: str | None = Header(default=None),
    db: aiosqlite.Connection = Depends(get_db),
):
    runner = await _runner_auth(x_runner_token, db)
    row = await repo.get_runner_job(db, job_id)
    if row is None or row["runner_id"] != runner["id"]:
        raise HTTPException(status_code=404, detail="Job not found")
    settled = await repo.finish_runner_job(
        db, job_id, ok=body.ok,
        result={"output": body.output, "handles": body.handles} if body.ok else None,
        error=body.error,
    )
    if not settled:
        raise HTTPException(status_code=409, detail="Job already finished")
    return {"ok": True}
