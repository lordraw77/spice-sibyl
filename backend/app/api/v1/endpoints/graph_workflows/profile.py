"""Profile-scoped resources: secrets, cross-workflow stats, token/run budget.

Extracted verbatim from the former single-file graph_workflows.py.
"""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import settings
from app.db import audit_repository, graph_workflow_repository as repo
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.schemas.auth import UserOut
from app.schemas.graph_workflows import (
    ProfileBudgetIn,
    ProfileBudgetOut,
    WorkflowStatsOut,
    WorkflowSecretIn,
    WorkflowSecretOut,
)

from ._common import _client_ip

router = APIRouter()

# ── secrets (Phase 32 — roadmap fase 1) ─────────────────────────────────────
# Profile-scoped, Fernet-encrypted at rest, referenced in node params as
# ``{{ $secrets.<name> }}``. Static paths declared before ``/{wf_id}``.

@router.get("/secrets", response_model=list[WorkflowSecretOut])
async def list_secrets(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Names + timestamps only — a stored secret value is never returned."""
    return await repo.list_secrets(db, profile_id)


@router.put("/secrets", response_model=WorkflowSecretOut)
async def put_secret(
    body: WorkflowSecretIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    """Create or replace one secret (upsert by name)."""
    from app.services import vault_service

    encrypted = vault_service.encrypt(body.value, settings.vault_secret_key)
    await repo.upsert_secret(db, profile_id, body.name, encrypted)
    await audit_repository.record(
        db, user.id, "graph_workflow.secret.put", resource=body.name, ip=_client_ip(request)
    )
    for row in await repo.list_secrets(db, profile_id):
        if row["name"] == body.name:
            return row
    raise HTTPException(status_code=500, detail="Secret not stored")


@router.delete("/secrets/{name}", status_code=204)
async def delete_secret(
    name: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    if not await repo.delete_secret(db, profile_id, name):
        raise HTTPException(status_code=404, detail="Secret not found")
    await audit_repository.record(
        db, user.id, "graph_workflow.secret.delete", resource=name, ip=_client_ip(request)
    )


# ── stats, import & generation (Phase 36 — roadmap fase 5) ──────────────────
# Static paths declared before ``/{wf_id}`` so they aren't swallowed by it.

@router.get("/stats", response_model=list[WorkflowStatsOut])
async def workflow_stats(
    environment: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """Fase 5.1 — per-workflow aggregates: run counts, success rate, average
    duration and LLM token totals (from the `_usage` key of llm.* node runs).
    ``environment`` (fase 7.2, extending fase 5.1) optionally scopes every
    aggregate to runs executed in that named environment — e.g. checking
    `prod` health separately from the unfiltered (all-environments) totals."""
    return await repo.workflow_stats_for_profile(db, profile_id, environment=environment or None)


# ── Phase 44 — budgets and quotas (roadmap fase 12.1) ───────────────────────
# Static path, declared before the dynamic ``/{wf_id}`` route.

@router.get("/budget", response_model=ProfileBudgetOut)
async def get_profile_budget(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    """The profile-wide ("workspace") LLM token / run cap for the calendar
    month, on top of any per-workflow cap — the caller is gated by whichever
    of the two is tighter."""
    budget = await repo.get_profile_budget(db, profile_id)
    return ProfileBudgetOut(
        profile_id=profile_id,
        token_budget_month=budget["token_budget_month"] if budget else None,
        run_budget_month=budget["run_budget_month"] if budget else None,
    )


@router.put("/budget", response_model=ProfileBudgetOut)
async def put_profile_budget(
    body: ProfileBudgetIn,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),  # noqa: ARG001 — enforce auth
):
    await repo.set_profile_budget(db, profile_id, body.token_budget_month, body.run_budget_month)
    return ProfileBudgetOut(
        profile_id=profile_id,
        token_budget_month=body.token_budget_month,
        run_budget_month=body.run_budget_month,
    )
