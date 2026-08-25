"""Launching a workflow: manual run and chat turn.

Extracted verbatim from the former single-file graph_workflows.py.
"""

import secrets

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import settings
from app.db import audit_repository, graph_workflow_repository as repo
from app.db.database import get_db
from app.dependencies.auth import get_current_user, resolve_profile
from app.schemas.auth import UserOut
from app.schemas.graph_workflows import WorkflowChatIn, WorkflowChatOut, RunTriggerIn
from app.services import workflow_graph_service as engine

from ._common import _client_ip, _owned

router = APIRouter()

# ── running ─────────────────────────────────────────────────────────────────

@router.post("/{wf_id}/run")
async def run_now(
    wf_id: str,
    body: RunTriggerIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),
):
    wf = await _owned(db, wf_id, profile_id)
    try:
        run_id = await engine.run_workflow(
            db, wf_id, profile_id,
            trigger_type="partial" if body.start_node_id else "manual",
            trigger_payload=body.payload, graph=wf.graph,
            start_node_id=body.start_node_id,
            environment=body.environment,
            debug=body.debug, breakpoints=body.breakpoints,
            priority=body.priority,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_repository.record(db, user.id, "graph_workflow.run", resource=wf_id, ip=_client_ip(request))
    return {"run_id": run_id}


@router.post("/{wf_id}/chat", response_model=WorkflowChatOut)
async def chat_turn(
    wf_id: str,
    body: WorkflowChatIn,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    profile_id: str = Depends(resolve_profile),
    user: UserOut = Depends(get_current_user),  # noqa: ARG001 — enforce auth
):
    """Fase 9.3 — one conversation turn against a ``chat``-triggered workflow.
    Runs the workflow synchronously with $trigger = {session_id, message,
    history}; the terminal ``chat.reply`` node's text comes back as ``reply``
    and the turn is appended to the session history (persisted, TTL-purged)."""
    wf = await _owned(db, wf_id, profile_id)
    triggers = await repo.list_triggers(db, wf_id)
    if not any(t.type == "chat" for t in triggers):
        raise HTTPException(status_code=400, detail="This workflow has no 'chat' trigger.")

    session_id = (body.session_id or secrets.token_urlsafe(12))[:128]
    history = await repo.get_chat_history(db, wf_id, session_id)
    trigger_payload = {"session_id": session_id, "message": body.message, "history": history}
    try:
        result = await engine.run_workflow_sync(
            db, wf_id, profile_id, trigger_type="chat", trigger_payload=trigger_payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get("status") != "completed":
        raise HTTPException(
            status_code=502,
            detail=f"Chat workflow did not complete ({result.get('status')}): {result.get('error') or 'unknown error'}",
        )
    reply = result.get("reply")
    if reply is None:
        reply = ""  # graph without a chat.reply node — still records the turn
    max_turns = max(1, int(settings.graph_workflow_chat_history_max_turns))
    history = (history + [
        {"role": "user", "content": body.message},
        {"role": "assistant", "content": reply},
    ])[-2 * max_turns:]
    await repo.upsert_chat_history(db, wf_id, profile_id, session_id, history)
    _ = wf  # ownership already enforced
    return WorkflowChatOut(session_id=session_id, reply=reply, run_id=result["run_id"])
