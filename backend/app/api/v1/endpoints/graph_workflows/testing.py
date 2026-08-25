"""Test suites, dry-run, cost estimate, budget status, single-node test and
expression preview.

Extracted verbatim from the former single-file graph_workflows.py.
"""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.db import audit_repository, graph_workflow_repository as repo
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.schemas.auth import UserOut
from app.schemas.graph_workflows import (
    ExpressionPreviewIn,
    WorkflowBudgetStatusOut,
    WorkflowCostEstimateOut,
    WorkflowDryRunIn,
    WorkflowDryRunOut,
    WorkflowTestCaseIn,
    WorkflowTestCaseOut,
    NodeTestIn,
    TestSuiteRunOut,
)
from app.services import workflow_graph_service as engine

from ._common import _client_ip, _owned

router = APIRouter()

# ── test suites (roadmap fase 11.1) ─────────────────────────────────────────

@router.get("/{wf_id}/test-cases", response_model=list[WorkflowTestCaseOut])
async def list_test_cases(
    wf_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    await _owned(db, wf_id, profile_id)
    return await repo.list_test_cases(db, wf_id)


@router.post("/{wf_id}/test-cases", response_model=WorkflowTestCaseOut, status_code=201)
async def create_test_case(
    wf_id: str,
    body: WorkflowTestCaseIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    await _owned(db, wf_id, profile_id)
    case_id = await repo.create_test_case(
        db, wf_id, body.name, body.trigger_payload,
        [a.model_dump() for a in body.assertions],
    )
    await audit_repository.record(
        db, user.id, "graph_workflow.test_case.create", resource=wf_id, detail=case_id, ip=_client_ip(request)
    )
    return await repo.get_test_case(db, case_id)


@router.put("/{wf_id}/test-cases/{case_id}", response_model=WorkflowTestCaseOut)
async def update_test_case(
    wf_id: str,
    case_id: str,
    body: WorkflowTestCaseIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    await _owned(db, wf_id, profile_id)
    existing = await repo.get_test_case(db, case_id)
    if existing is None or existing.workflow_id != wf_id:
        raise HTTPException(status_code=404, detail="Test case not found")
    await repo.update_test_case(
        db, case_id, body.name, body.trigger_payload,
        [a.model_dump() for a in body.assertions],
    )
    await audit_repository.record(
        db, user.id, "graph_workflow.test_case.update", resource=wf_id, detail=case_id, ip=_client_ip(request)
    )
    return await repo.get_test_case(db, case_id)


@router.delete("/{wf_id}/test-cases/{case_id}", status_code=204)
async def delete_test_case(
    wf_id: str,
    case_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    await _owned(db, wf_id, profile_id)
    existing = await repo.get_test_case(db, case_id)
    if existing is None or existing.workflow_id != wf_id:
        raise HTTPException(status_code=404, detail="Test case not found")
    await repo.delete_test_case(db, case_id)
    await audit_repository.record(
        db, user.id, "graph_workflow.test_case.delete", resource=wf_id, detail=case_id, ip=_client_ip(request)
    )


@router.post("/{wf_id}/test-cases/run", response_model=TestSuiteRunOut)
async def run_test_suite(
    wf_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Fase 11.1 — run every saved test case: each executes the workflow with
    its fixture $trigger (pinned nodes replace external calls) and checks its
    assertions against the actual node outputs."""
    await _owned(db, wf_id, profile_id)
    result = await engine.run_test_suite(db, wf_id, profile_id)
    await audit_repository.record(
        db, user.id, "graph_workflow.test_suite.run", resource=wf_id,
        detail=f"{result.passed}/{result.total} passed", ip=_client_ip(request),
    )
    return result


# ── dry-run (roadmap fase 11.2) ──────────────────────────────────────────────

@router.post("/{wf_id}/dry-run", response_model=WorkflowDryRunOut)
async def dry_run(
    wf_id: str,
    body: WorkflowDryRunIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Fase 11.2 — simulate the whole graph: http.request/db.query/
    notification.*/llm.* are mocked (pinned output when present, else a typed
    placeholder) so nothing external actually happens. Use before activating
    a schedule on a new graph."""
    await _owned(db, wf_id, profile_id)
    try:
        result = await engine.dry_run_workflow(db, wf_id, profile_id, body.payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_repository.record(
        db, user.id, "graph_workflow.dry_run", resource=wf_id, ip=_client_ip(request)
    )
    return result


# ── cost estimate (roadmap fase 11.3) ────────────────────────────────────────

@router.get("/{wf_id}/cost-estimate", response_model=WorkflowCostEstimateOut)
async def cost_estimate(
    wf_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Fase 11.3 — static tokens/month projection from historical LLM usage
    (fase 5.1/7.4) and the workflow's active schedule frequency."""
    await _owned(db, wf_id, profile_id)
    try:
        return await engine.cost_estimate(db, wf_id, profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{wf_id}/budget", response_model=WorkflowBudgetStatusOut)
async def workflow_budget(
    wf_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Fase 12.1 — this workflow's own budget caps and usage for the current
    period, plus the profile-wide ("workspace") cap it is also gated by."""
    await _owned(db, wf_id, profile_id)
    try:
        return await engine.budget_status(db, wf_id, profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{wf_id}/nodes/{node_id}/test")
async def test_node(
    wf_id: str,
    node_id: str,
    body: NodeTestIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Roadmap fase 3.1 — execute a single node with its current (or mocked)
    input and return the output inline; no run is recorded. ``body.node``
    may carry the unsaved editor state of the node; ``body.input`` mocks
    its primary input. Node failures come back as ``{ok: false, error}``."""
    await _owned(db, wf_id, profile_id)
    if body.node is not None and body.node.id != node_id:
        raise HTTPException(status_code=400, detail="node id mismatch")
    try:
        result = await engine.test_node(
            db, wf_id, profile_id, node_id,
            node_override=body.node, input_override=body.input,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await audit_repository.record(
        db, user.id, "graph_workflow.node.test", resource=f"{wf_id}/{node_id}", ip=_client_ip(request)
    )
    return result


@router.post("/{wf_id}/preview-expression")
async def preview_expression(
    wf_id: str,
    body: ExpressionPreviewIn,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Evaluate an expression read-only against the workflow's latest run data
    (node outputs + trigger). Returns {ok, value} or {ok, error} — never 500s
    on a bad expression, so the editor can show the message inline."""
    await _owned(db, wf_id, profile_id)
    return await engine.preview_expression(db, wf_id, body.expression)
