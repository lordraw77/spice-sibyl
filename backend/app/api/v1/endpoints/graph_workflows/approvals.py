"""Human-in-the-loop inbox: approvals, human input and wait.event delivery.

Extracted verbatim from the former single-file graph_workflows.py.
"""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.db import audit_repository, graph_workflow_repository as repo
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.schemas.auth import UserOut
from app.schemas.graph_workflows import (
    ApprovalDecisionIn,
    EventDeliverIn,
    HumanInputSubmitIn,
    WorkflowApprovalOut,
)
from app.services import workflow_graph_service as engine

from ._common import _client_ip

router = APIRouter()

# ── approvals (Phase 35 — roadmap fase 4.4) ─────────────────────────────────
# Static paths declared before ``/{wf_id}`` so they aren't swallowed by it.

@router.get("/approvals", response_model=list[WorkflowApprovalOut])
async def list_approvals(
    status: str | None = "pending",
    run_id: str | None = None,
    kind: str | None = None,
    limit: int = 100,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """The profile's human-in-the-loop requests, newest first (``status=pending``
    by default; pass ``status=`` empty for all). ``run_id`` scopes the list to
    one run — the runs page uses it to render the approve/reject/submit panel.
    ``kind`` (fase 10) scopes to approval|input|event."""
    return await repo.list_approvals(
        db, profile_id, status=status or None, run_id=run_id, kind=kind or None, limit=min(max(limit, 1), 500)
    )


@router.post("/approvals/{approval_id}/decision", response_model=WorkflowApprovalOut)
async def decide_approval(
    approval_id: str,
    body: ApprovalDecisionIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Approve or reject a pending request. The suspended run picks the decision
    up within a couple of seconds and continues down the matching branch."""
    approval = await repo.get_approval(db, approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.profile_id != profile_id:
        # Fase 7.3 — a workspace member holding the 'approver' share role on the
        # workflow may decide its approvals even without owning it.
        from app.db import workspace_repository

        role = await workspace_repository.get_workflow_share_role(db, approval.workflow_id, user.id)
        if role != "approver":
            raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail=f"Approval is already {approval.status}")
    decided = await repo.decide_approval(
        db, approval_id,
        status="approved" if body.approved else "rejected",
        decided_by=user.id, comment=body.comment,
    )
    if not decided:  # raced the engine's timeout poll — first writer wins
        raise HTTPException(status_code=409, detail="Approval was just settled")
    await audit_repository.record(
        db, user.id, "graph_workflow.approval.decide", resource=approval_id, ip=_client_ip(request)
    )
    return await repo.get_approval(db, approval_id)


# ── human input & wait.event (Phase 42 — roadmap fase 10) ──────────────────

@router.post("/approvals/{approval_id}/submit", response_model=WorkflowApprovalOut)
async def submit_human_input(
    approval_id: str,
    body: HumanInputSubmitIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Submit the form of a pending ``human.input`` request (fase 10.1). ``data``
    is validated against the request's JSON Schema before it is accepted; the
    suspended run picks it up within a couple of seconds and continues."""
    approval = await repo.get_approval(db, approval_id)
    if not approval or approval.kind != "input":
        raise HTTPException(status_code=404, detail="Input request not found")
    if approval.profile_id != profile_id:
        from app.db import workspace_repository

        role = await workspace_repository.get_workflow_share_role(db, approval.workflow_id, user.id)
        if role != "approver":
            raise HTTPException(status_code=404, detail="Input request not found")
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail=f"Request is already {approval.status}")
    if approval.form_schema:
        violations = engine._validate_json_schema(body.data, approval.form_schema)
        if violations:
            raise HTTPException(
                status_code=422, detail="Data does not match the requested schema: " + "; ".join(violations[:5])
            )
    decided = await repo.decide_approval(
        db, approval_id, status="submitted", decided_by=user.id, comment=body.comment, data=body.data,
    )
    if not decided:  # raced the engine's timeout poll — first writer wins
        raise HTTPException(status_code=409, detail="Request was just settled")
    await audit_repository.record(
        db, user.id, "graph_workflow.input.submit", resource=approval_id, ip=_client_ip(request)
    )
    return await repo.get_approval(db, approval_id)


@router.post("/events/{correlation_id}", response_model=WorkflowApprovalOut)
async def deliver_event(
    correlation_id: str,
    body: EventDeliverIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Deliver an external event to a suspended ``wait.event`` node (fase 10.2).
    ``payload`` becomes the node's output; the run picks it up within a couple
    of seconds and continues down 'main'."""
    approval = await repo.get_pending_event(db, correlation_id, profile_id)
    if not approval:
        raise HTTPException(status_code=404, detail="No pending wait.event for this correlation id")
    decided = await repo.decide_approval(
        db, approval.id, status="delivered", decided_by=user.id, data=body.payload,
    )
    if not decided:  # raced the engine's timeout poll — first writer wins
        raise HTTPException(status_code=409, detail="Request was just settled")
    await audit_repository.record(
        db, user.id, "graph_workflow.event.deliver", resource=correlation_id, ip=_client_ip(request)
    )
    return await repo.get_approval(db, approval.id)
