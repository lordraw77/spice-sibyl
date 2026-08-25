"""Telegram command -> workflow bindings (Phase 52 / roadmap fase 20.5).

Extracted verbatim from the former single-file graph_workflows.py.
"""

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Response

from app.db import graph_workflow_repository as repo
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.schemas.auth import UserOut
from app.schemas.graph_workflows import TelegramBindingIn, TelegramBindingOut

from ._common import _owned

router = APIRouter()

# ── telegram command bindings (Phase 52 / roadmap fase 20.5) ──────────────────

@router.get("/telegram-bindings", response_model=list[TelegramBindingOut])
async def list_telegram_bindings(
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
):
    return [TelegramBindingOut(**b) for b in await repo.list_telegram_bindings(db, profile_id)]


@router.post("/telegram-bindings", response_model=TelegramBindingOut, status_code=201)
async def create_telegram_binding(
    body: TelegramBindingIn,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),  # noqa: ARG001 — enforce auth
):
    """Bind a bot command (``/report``) to a workflow (fase 20.5). A command
    already claimed in this profile is a 409."""
    await _owned(db, body.workflow_id, profile_id)
    try:
        binding = await repo.create_telegram_binding(
            db, profile_id, body.command, body.workflow_id, body.description
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return TelegramBindingOut(**binding)


@router.delete("/telegram-bindings/{command}", status_code=204)
async def delete_telegram_binding(
    command: str,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),  # noqa: ARG001 — enforce auth
):
    if not await repo.delete_telegram_binding(db, profile_id, command):
        raise HTTPException(status_code=404, detail="Binding not found")
    return Response(status_code=204)
