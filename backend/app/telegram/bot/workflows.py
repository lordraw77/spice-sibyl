"""Telegram as a workflow channel (Phase 52): /run, command bindings,
inbound media, approval and telegram.ask callbacks.

Extracted from the former single-file bot.py.
"""

import logging

import aiosqlite
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, ContextTypes
import secrets

from app.core.config import settings

from .account import _linked_profile_id
from .state import _is_allowed
from .ui import _BOT_COMMANDS

logger = logging.getLogger(__name__)

# ── Workflow approvals (Phase 39 — roadmap fase 7.5) ─────────────────────────

async def _cb_workflow_approval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline Approve/Reject on a human.approval notification. The decision is
    equivalent to POST /approvals/{id}/decision: the chat must be linked to the
    profile that owns the request, first writer wins on races, and the suspended
    run resumes down the matching branch within the engine's poll interval."""
    from app.db import graph_workflow_repository as gw_repo
    from app.db import telegram_link_repository as tl_repo

    query = update.callback_query
    data = str(query.data or "")
    try:
        _prefix, verdict, approval_id = data.split(":", 2)
    except ValueError:
        await query.answer()
        return
    chat_id = query.message.chat_id

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        link = await tl_repo.get_by_telegram_id(db, chat_id)
        approval = await gw_repo.get_approval(db, approval_id)
        if approval is None or link is None or link["profile_id"] != approval.profile_id:
            await query.answer("Richiesta non trovata / not authorized", show_alert=True)
            return
        if approval.status != "pending":
            await query.answer(f"Già decisa: {approval.status}", show_alert=True)
            await query.edit_message_reply_markup(reply_markup=None)
            return
        status = "approved" if verdict == "a" else "rejected"
        decided = await gw_repo.decide_approval(
            db, approval_id, status=status,
            decided_by=f"telegram:{update.effective_user.id if update.effective_user else chat_id}",
            comment="via Telegram",
        )
        if not decided:  # raced the timeout poll or the web UI — first writer wins
            await query.answer("Già decisa nel frattempo", show_alert=True)
            await query.edit_message_reply_markup(reply_markup=None)
            return

    await query.answer("✅ Approvato" if status == "approved" else "❌ Rifiutato")
    try:
        await query.edit_message_text(
            f"{query.message.text}\n\n{'✅ Approvato' if status == 'approved' else '❌ Rifiutato'} via Telegram"
        )
    except Exception:  # noqa: BLE001 — message edits are cosmetic, the decision is stored
        await query.edit_message_reply_markup(reply_markup=None)


# ── Phase 52 (roadmap fase 20) — Telegram as a workflow channel ──────────────

async def save_inbound_telegram_file(bot, tg_file, name: str, prefix: str = "telegram") -> dict | None:
    """20.4 — fetch an inbound document/photo/voice/video into the workspace
    storage (``GRAPH_WORKFLOW_FILES_DIR``) so ``file.*`` / ``doc.convert`` /
    ``kb.search`` can consume it. Returns ``{path, mime, name, size}`` (path
    relative to the storage root) or None when the file is over the size limit."""
    import os

    from app.services import workflow_graph_service as engine

    size = getattr(tg_file, "file_size", None) or 0
    max_bytes = settings.graph_workflow_telegram_max_file_mb * 1024 * 1024
    if size and size > max_bytes:
        logger.warning("save_inbound_telegram_file: %s over limit (%d bytes)", name, size)
        return None
    rel = f"{prefix}/{secrets.token_hex(8)}_{os.path.basename(name)}"
    dest = engine._safe_workspace_path(rel, create_dirs=True)
    file = await bot.get_file(tg_file.file_id)
    data = await file.download_as_bytearray()
    if len(data) > max_bytes:
        return None
    with open(dest, "wb") as fh:
        fh.write(bytes(data))
    return {
        "path": rel, "name": os.path.basename(name), "size": len(data),
        "mime": getattr(tg_file, "mime_type", None) or "application/octet-stream",
    }


async def run_telegram_workflow(
    workflow_id: str, profile_id: str, *, chat_id: int, text: str = "",
    command: str = "", args: list[str] | None = None, thread_id: int | None = None,
    user: dict | None = None, launched_via: str = "command", file: dict | None = None,
) -> dict:
    """Run a workflow from an inbound Telegram message and return its result
    (``{run_id, status, output, reply, error}``). ``$trigger`` carries the chat
    context (20.1). Runs inline so the terminal ``chat.reply`` / ``telegram.*``
    output can be returned to the originating chat."""
    from app.services import workflow_graph_service as engine

    payload: dict = {
        "chat_id": chat_id, "thread_id": thread_id, "text": text, "command": command,
        "args": args or [], "user": user or {}, "launched_via": launched_via,
    }
    if file is not None:
        payload["file"] = file
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        return await engine.run_workflow_sync(
            db, workflow_id, profile_id, trigger_type="telegram", trigger_payload=payload,
        )


async def _reply_from_result(chat_id: int, result: dict, send) -> None:
    """Send a workflow's terminal reply back to the chat (``reply`` from a
    chat.reply node, else a compact status line). ``send`` is ``bot.send_message``."""
    reply = result.get("reply")
    if not reply:
        if result.get("status") == "failed":
            reply = f"⚠ Workflow failed: {result.get('error') or 'unknown error'}"
        else:
            reply = "✅ Done." if result.get("status") == "completed" else f"Status: {result.get('status')}"
    await send(chat_id=chat_id, text=str(reply)[:4096])


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """20.1 — generic launcher. ``/run`` lists the active workflows the sender may
    launch as an inline keyboard; ``/run <name-or-id> [args…]`` launches directly."""
    user = update.effective_user
    if not _is_allowed(user.id):
        return
    chat_id = update.effective_chat.id
    profile_id = await _linked_profile_id(user.id)
    if not profile_id:
        await update.message.reply_text("🔗 Link a web profile first with /link.")
        return

    from app.db import graph_workflow_repository as gw_repo

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        workflows = [w for w in await gw_repo.list_workflows(db, profile_id) if w.active]

    args = [a for a in (context.args or []) if a.strip()]
    if not args:
        if not workflows:
            await update.message.reply_text("No active workflows to run.")
            return
        rows = [[InlineKeyboardButton(w.name[:60], callback_data=f"wfrun:{w.id}")] for w in workflows[:20]]
        await update.message.reply_text(
            "Pick a workflow to run:", reply_markup=InlineKeyboardMarkup(rows)
        )
        return

    target = args[0].lower()
    match = next((w for w in workflows if w.id == args[0] or w.name.lower() == target), None)
    if match is None:
        await update.message.reply_text(f"No active workflow named '{args[0]}'.")
        return
    result = await run_telegram_workflow(
        match.id, profile_id, chat_id=chat_id, args=args[1:], launched_via="picker",
        user={"id": user.id, "username": user.username},
    )
    await _reply_from_result(chat_id, result, context.bot.send_message)


async def _cb_run_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback for a /run inline-keyboard pick."""
    query = update.callback_query
    await query.answer()
    workflow_id = str(query.data or "").split(":", 1)[-1]
    user = update.effective_user
    chat_id = query.message.chat_id
    profile_id = await _linked_profile_id(user.id)
    if not profile_id:
        return
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        from app.db import graph_workflow_repository as gw_repo

        wf = await gw_repo.get_workflow(db, workflow_id)
    if wf is None or wf.profile_id != profile_id or not wf.active:
        await query.edit_message_text("Workflow not available.")
        return
    result = await run_telegram_workflow(
        workflow_id, profile_id, chat_id=chat_id, launched_via="picker",
        user={"id": user.id, "username": user.username},
    )
    await _reply_from_result(chat_id, result, context.bot.send_message)


async def _dispatch_workflow_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """20.1 — catch-all for a bot command bound to a workflow (``/report`` →
    workflow via the fase 20.5 registry). Registered last so builtin commands win.
    An unbound command is silently ignored (the default chat loop never sees a
    command). Dedup by (chat_id, message_id) guards a double delivery (16.2)."""
    msg = update.message
    if not msg or not msg.text or not msg.text.startswith("/"):
        return
    user = update.effective_user
    if not _is_allowed(user.id):
        return
    command = msg.text[1:].split()[0].split("@")[0].lower()
    args = msg.text.split()[1:]

    from app.db import graph_workflow_repository as gw_repo

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        binding = await gw_repo.find_telegram_binding_by_command(db, command)
        if binding is None:
            return
        wf = await gw_repo.get_workflow(db, binding["workflow_id"])
    profile_id = await _linked_profile_id(user.id)
    if wf is None or not wf.active or (profile_id and wf.profile_id != profile_id):
        return

    file_meta = None
    if msg.document:
        file_meta = await save_inbound_telegram_file(context.bot, msg.document, msg.document.file_name or "file")
    result = await run_telegram_workflow(
        wf.id, wf.profile_id, chat_id=update.effective_chat.id, text=msg.text,
        command=command, args=args, thread_id=getattr(msg, "message_thread_id", None),
        user={"id": user.id, "username": user.username}, file=file_meta,
    )
    await _reply_from_result(update.effective_chat.id, result, context.bot.send_message)


async def _cb_telegram_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """20.3 — a tap on a ``telegram.ask`` inline button delivers the chosen value
    into the suspended run (reusing the wait.event correlation path) and clears
    the client spinner."""
    query = update.callback_query
    try:
        _prefix, approval_id, value = str(query.data or "").split(":", 2)
    except ValueError:
        await query.answer()
        return
    from app.db import graph_workflow_repository as gw_repo

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        approval = await gw_repo.get_approval(db, approval_id)
        if approval is None or approval.status != "pending":
            await query.answer("Already answered", show_alert=False)
            await query.edit_message_reply_markup(reply_markup=None)
            return
        await gw_repo.decide_approval(
            db, approval_id, status="delivered",
            decided_by=f"telegram:{update.effective_user.id if update.effective_user else ''}",
            data={"value": value},
        )
    await query.answer("✅")
    try:
        await query.edit_message_text(f"{query.message.text}\n\n➡️ {value}")
    except Exception:  # noqa: BLE001 — cosmetic
        await query.edit_message_reply_markup(reply_markup=None)


async def register_workflow_bot_commands(app: Application) -> None:
    """20.5 — register command↔workflow bindings via setMyCommands so bound
    commands (``/report``) appear in the Telegram UI alongside builtins."""
    from telegram import BotCommand

    try:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            from app.db import graph_workflow_repository as gw_repo

            bindings = await gw_repo.list_all_telegram_bindings(db)
        if not bindings:
            return
        extra = [BotCommand(b["command"], (b["description"] or f"Run {b['command']}")[:256]) for b in bindings]
        await app.bot.set_my_commands(_BOT_COMMANDS + extra)
    except Exception:  # noqa: BLE001 — a binding hiccup must not break boot
        logger.warning("register_workflow_bot_commands: failed to register bindings", exc_info=True)
