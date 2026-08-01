"""
Human-in-the-loop & async-wait node family: ``human.approval`` (fase 4.4),
``human.input`` (fase 10.1), ``wait.event`` (fase 10.2) and ``telegram.ask``
(fase 20.3).

These are the "entangled" nodes of the god-object explosion (roadmap §2.1/§4.1):
unlike the pure families, each one *suspends the run* — it creates a
``workflow_approvals`` row, flips the run to ``waiting`` on the SSE bus, and
polls until the row is decided or times out. The suspend/resume contract lives
here, self-contained, importing only:

* ``app.workflow.bus`` — publish the ``waiting``/``running`` lifecycle events
  (never the engine → no cycle);
* ``app.workflow.nodes.messaging`` — the shared Telegram/notify helpers that
  already moved out of the engine;
* the graph-workflow repository (the ``workflow_approvals`` table).

The fase 2.4 resume machinery re-executes a suspended node through the normal
DAG walk → dispatch table, so ``get_pending_approval`` re-attaches to the
existing row instead of creating a duplicate after a restart.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiosqlite

from app.core.config import settings
from app.db import graph_workflow_repository as repo
from app.schemas.graph_workflows import GraphNode, WorkflowApprovalOut
from app.workflow.bus import publish as _publish
from app.workflow.context import _as_bool
from app.workflow.nodes.messaging import _notify_text, _resolve_chat_id, _telegram_bot
from app.workflow.registry import DispatchCtx, node

logger = logging.getLogger(__name__)

_APPROVAL_POLL_SECONDS = 2.0        # how often a waiting human.approval re-checks its request


async def _notify_approval_request(
    db: aiosqlite.Connection, profile_id: str, approval, params: dict
) -> None:
    """Best-effort notification that a decision is awaited — a broken channel
    must never fail the node (the request row itself is the source of truth)."""
    from app.services import notification_service

    body = approval.message or "Apri Workflow → Esecuzioni per approvare o rifiutare."
    try:
        await notification_service.notify_web(db, profile_id, "workflow", approval.title, body)
    except Exception:  # noqa: BLE001
        logger.exception("human.approval: in-app notification failed for %s", approval.id)
    if _as_bool(params.get("telegram")):
        try:
            # Fase 7.5 — inline Approve/Reject buttons: the bot callback decides
            # the request exactly like POST /approvals/{id}/decision would.
            await notification_service.notify_telegram(
                db, profile_id, "workflow", f"{approval.title}\n{body}",
                buttons=[[
                    ("✅ Approve", f"wfap:a:{approval.id}"),
                    ("❌ Reject", f"wfap:r:{approval.id}"),
                ]],
            )
        except Exception:  # noqa: BLE001
            logger.exception("human.approval: telegram notification failed for %s", approval.id)


async def _wait_for_decision(db: aiosqlite.Connection, run_id: str, approval) -> WorkflowApprovalOut:
    """Suspend the run until ``approval`` is decided or ``timeout_at`` passes
    (roadmap fase 4.4; shared by human.approval/human.input/wait.event since
    fase 10). Flips the run to ``waiting`` then back to ``running`` around the
    poll — a cancelled/failing run overwrites this right after in _execute's
    handlers."""
    await repo.set_run_status(db, run_id, "waiting")
    _publish(run_id, {"kind": "run", "status": "waiting", "approval_id": approval.id})
    try:
        while True:
            current = await repo.get_approval(db, approval.id)
            if current is None:
                raise RuntimeError("waiting request row disappeared")
            if current.status != "pending":
                return current
            if current.timeout_at is not None and time.time() >= current.timeout_at:
                # First writer wins: the poll may race the decision endpoint.
                await repo.decide_approval(db, approval.id, status="expired")
                return await repo.get_approval(db, approval.id) or current
            await asyncio.sleep(_APPROVAL_POLL_SECONDS)
    finally:
        await repo.set_run_status(db, run_id, "running")
        _publish(run_id, {"kind": "run", "status": "running"})


async def _exec_human_approval(
    db: aiosqlite.Connection, profile_id: str, node: GraphNode, params: dict, ctx: dict
) -> tuple[object, list[str]]:
    """Suspend the run until a human decides (roadmap fase 4.4). Creates a
    ``workflow_approvals`` row (or re-attaches to the pending one after a
    restart — the fase 2.4 resume machinery re-executes this node), flips the
    run to ``waiting``, notifies, then polls the row until it is decided or
    ``timeout_at`` passes. Routes through the ``approved``/``rejected`` handle;
    a timeout follows ``onTimeout`` (reject | fail)."""
    run_id = ctx.get("_run_id")
    if not run_id:
        raise ValueError("human.approval: only runs inside a real execution (single-node test unsupported)")
    run = await repo.get_run(db, run_id)
    workflow_id = run.workflow_id if run else ""
    wf = await repo.get_workflow(db, workflow_id) if workflow_id else None

    approval = await repo.get_pending_approval(db, run_id, node.id)
    if approval is None:
        timeout_s = float(params.get("timeout") or 86400)
        timeout_s = max(1.0, min(timeout_s, float(settings.graph_workflow_approval_max_timeout)))
        title = str(params.get("title") or "").strip() or (
            f"Approvazione richiesta: {wf.name}" if wf else "Approvazione richiesta"
        )
        message = _notify_text(params)
        approval = await repo.create_approval(
            db, run_id, node.id, workflow_id, profile_id,
            title=title, message=message, timeout_at=int(time.time() + timeout_s),
        )
        await _notify_approval_request(db, profile_id, approval, params)

    current = await _wait_for_decision(db, run_id, approval)

    output = {
        "approved": current.status == "approved",
        "status": current.status,
        "comment": current.comment,
        "decided_by": current.decided_by,
        "approval_id": current.id,
        "title": current.title,
    }
    if current.status == "approved":
        return output, ["approved"]
    if current.status == "expired" and str(params.get("onTimeout") or "reject").lower() == "fail":
        raise RuntimeError("human.approval: request expired without a decision")
    return output, ["rejected"]


async def _exec_human_input(
    db: aiosqlite.Connection, profile_id: str, node: GraphNode, params: dict, ctx: dict
) -> tuple[object, list[str]]:
    """Suspend the run until a human fills a form (roadmap fase 10.1). Like
    ``human.approval`` but the request carries a JSON Schema (``schema`` param)
    and the run resumes with the submitted data as ``{data}``, validated by
    POST /approvals/{id}/submit before it is accepted. Routes through
    ``submitted``; a timeout follows ``onTimeout`` (branch | fail)."""
    run_id = ctx.get("_run_id")
    if not run_id:
        raise ValueError("human.input: only runs inside a real execution (single-node test unsupported)")
    run = await repo.get_run(db, run_id)
    workflow_id = run.workflow_id if run else ""
    wf = await repo.get_workflow(db, workflow_id) if workflow_id else None

    approval = await repo.get_pending_approval(db, run_id, node.id)
    if approval is None:
        timeout_s = float(params.get("timeout") or 86400)
        timeout_s = max(1.0, min(timeout_s, float(settings.graph_workflow_approval_max_timeout)))
        title = str(params.get("title") or "").strip() or (
            f"Input richiesto: {wf.name}" if wf else "Input richiesto"
        )
        form_schema = params.get("schema")
        if form_schema is not None and not isinstance(form_schema, dict):
            raise ValueError("human.input: 'schema' must be a JSON Schema object")
        message = _notify_text(params)
        approval = await repo.create_approval(
            db, run_id, node.id, workflow_id, profile_id,
            title=title, message=message, timeout_at=int(time.time() + timeout_s),
            kind="input", schema=form_schema,
        )
        await _notify_approval_request(db, profile_id, approval, params)

    current = await _wait_for_decision(db, run_id, approval)

    if current.status == "submitted":
        output = {
            "data": current.data, "status": current.status, "comment": current.comment,
            "decided_by": current.decided_by, "approval_id": current.id, "title": current.title,
        }
        return output, ["submitted"]
    if current.status == "expired" and str(params.get("onTimeout") or "branch").lower() == "fail":
        raise RuntimeError("human.input: request expired without a submission")
    return {"data": None, "status": current.status, "approval_id": current.id}, ["timeout"]


async def _exec_wait_event(
    db: aiosqlite.Connection, profile_id: str, node: GraphNode, params: dict, ctx: dict
) -> tuple[object, list[str]]:
    """Suspend the run until an external event with a matching correlation id
    arrives via POST /events/{correlation_id} (roadmap fase 10.2). The run
    resumes with the delivered payload as the node output, through ``main``;
    a timeout follows ``onTimeout`` (branch | fail)."""
    run_id = ctx.get("_run_id")
    if not run_id:
        raise ValueError("wait.event: only runs inside a real execution (single-node test unsupported)")
    correlation_id = str(params.get("correlationId") or "").strip()
    if not correlation_id:
        raise ValueError("wait.event: 'correlationId' is required")
    run = await repo.get_run(db, run_id)
    workflow_id = run.workflow_id if run else ""

    approval = await repo.get_pending_approval(db, run_id, node.id)
    if approval is None:
        timeout_s = float(params.get("timeout") or 86400)
        timeout_s = max(1.0, min(timeout_s, float(settings.graph_workflow_approval_max_timeout)))
        approval = await repo.create_approval(
            db, run_id, node.id, workflow_id, profile_id,
            title=f"In attesa dell'evento '{correlation_id}'", message="",
            timeout_at=int(time.time() + timeout_s),
            kind="event", correlation_id=correlation_id,
        )

    current = await _wait_for_decision(db, run_id, approval)

    if current.status == "delivered":
        payload = current.data if isinstance(current.data, dict) else {"data": current.data}
        return payload, ["main"]
    if current.status == "expired" and str(params.get("onTimeout") or "branch").lower() == "fail":
        raise RuntimeError("wait.event: timed out waiting for the correlated event")
    return {}, ["timeout"]


async def _exec_telegram_ask(
    db: aiosqlite.Connection, profile_id: str, node: GraphNode, params: dict, ctx: dict
) -> tuple[object, list[str]]:
    """20.3 — present inline buttons on Telegram, suspend the run (reusing the
    ``wait.event`` correlation machinery), and resume with the tapped
    ``callback_data`` as output. ``onTimeout`` (branch|fail) governs a timeout."""
    run_id = ctx.get("_run_id")
    if not run_id:
        raise ValueError("telegram.ask: only runs inside a real execution")
    chat_id = _resolve_chat_id(params, ctx)
    question = str(params.get("text") or params.get("question") or "").strip()
    options = params.get("options")
    if not isinstance(options, list) or not options:
        raise ValueError("telegram.ask: 'options' must be a non-empty list of {label, value}")

    run = await repo.get_run(db, run_id)
    workflow_id = run.workflow_id if run else ""
    approval = await repo.get_pending_approval(db, run_id, node.id)
    if approval is None:
        correlation_id = f"tgask:{run_id}:{node.id}"
        timeout_s = float(params.get("timeout") or 3600)
        timeout_s = max(1.0, min(timeout_s, float(settings.graph_workflow_approval_max_timeout)))
        approval = await repo.create_approval(
            db, run_id, node.id, workflow_id, profile_id,
            title=question or "?", message="", timeout_at=int(time.time() + timeout_s),
            kind="event", correlation_id=correlation_id,
        )
        # Send the buttons; each callback_data carries the correlation id + value.
        bot = _telegram_bot()
        if bot is not None:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            rows = []
            for opt in options:
                label = str((opt or {}).get("label") or (opt or {}).get("value") or "")
                value = str((opt or {}).get("value") or label)
                rows.append([InlineKeyboardButton(label, callback_data=f"wfask:{approval.id}:{value}")])
            try:
                await bot.send_message(
                    chat_id=chat_id, text=question or "?",
                    reply_markup=InlineKeyboardMarkup(rows),
                )
            except Exception:  # noqa: BLE001 — a failed prompt still waits (deliverable via API)
                logger.warning("telegram.ask: failed to send prompt to chat %s", chat_id)

    current = await _wait_for_decision(db, run_id, approval)
    if current.status == "delivered":
        payload = current.data if isinstance(current.data, dict) else {"value": current.data}
        return payload, ["main"]
    if current.status == "expired" and str(params.get("onTimeout") or "branch").lower() == "fail":
        raise RuntimeError("telegram.ask: timed out waiting for a response")
    return {}, ["timeout"]


# ── handler registration ─────────────────────────────────────────────────────
# Each node suspends the run, so the handler returns the raw (output, handles)
# from its _exec_* (which already picks the active handle) rather than forcing
# ["main"] like the pure families do.

@node("human.approval")
async def _h_human_approval(c: DispatchCtx):
    return await _exec_human_approval(c.db, c.profile_id, c.node, c.params, c.ctx)


@node("human.input")  # Phase 42 (roadmap fase 10)
async def _h_human_input(c: DispatchCtx):
    return await _exec_human_input(c.db, c.profile_id, c.node, c.params, c.ctx)


@node("wait.event")
async def _h_wait_event(c: DispatchCtx):
    return await _exec_wait_event(c.db, c.profile_id, c.node, c.params, c.ctx)


@node("telegram.ask")
async def _h_telegram_ask(c: DispatchCtx):
    return await _exec_telegram_ask(c.db, c.profile_id, c.node, c.params, c.ctx)
