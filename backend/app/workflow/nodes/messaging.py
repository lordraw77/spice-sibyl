"""
Messaging & retrieval nodes: notify.telegram/email/webhook/inapp,
telegram.send/sendMedia/editMessage/deleteMessage, chat.reply, kb.search.

Leaf nodes — they call notification_service / the Telegram bot / rag_service,
never the run orchestration. The shared Telegram helpers (``_telegram_bot``,
``_resolve_chat_id``, ``_telegram_parse_mode``) and ``_notify_text`` live here
and are re-exported by the engine, because the orchestration-coupled
``telegram.ask`` node (which suspends the run) stays in the engine and reuses
them. ``notify.webhook`` delegates to the io family's http.request.
"""

from __future__ import annotations

import json
import re

from app.core.config import settings
from app.workflow.context import _as_bool, _safe_workspace_path
from app.workflow.nodes.io import _exec_http_request
from app.workflow.registry import DispatchCtx, node


# ── kb.search ────────────────────────────────────────────────────────────────

async def _exec_kb_search(
    db, profile_id: str, params: dict, node_input
) -> dict:
    """Fase 6.5 — semantic search over the profile's knowledge base (Phase 28),
    returning structured hits instead of the tool's flattened text: RAG inside
    workflows without going through a generic llm.agent."""
    from app.services import rag_service

    query = params.get("query")
    if query in (None, ""):
        query = node_input
    if query in (None, ""):
        raise ValueError("kb.search: 'query' is required")
    if not isinstance(query, str):
        query = json.dumps(query, default=str, ensure_ascii=False)
    query = query.strip()
    if not query:
        raise ValueError("kb.search: 'query' is required")
    top_k = max(1, min(int(params.get("top_k") or 5), 20))
    document_ids = params.get("document_ids")
    if isinstance(document_ids, str):
        document_ids = [d.strip() for d in document_ids.split(",") if d.strip()]
    if not isinstance(document_ids, list) or not document_ids:
        document_ids = None
    sources = await rag_service.retrieve(
        db, profile_id, query, top_k=top_k, document_ids=document_ids
    )
    results = [
        {
            "text": s.snippet,
            "score": round(float(s.score), 4),
            "source": s.filename,
            "chunk_index": s.chunk_index,
        }
        for s in sources
    ]
    return {"results": results, "count": len(results), "query": query}


# ── notification nodes ──────────────────────────────────────────────────────

def _notify_text(params: dict) -> str:
    text = params.get("text") or params.get("message") or params.get("body") or ""
    if not isinstance(text, str):
        text = json.dumps(text, default=str, ensure_ascii=False)
    return text


_TELEGRAM_PARSE_MODES = frozenset({"", "Markdown", "MarkdownV2", "HTML"})
# CommonMark-style **bold** (what LLM nodes typically produce) isn't valid Telegram
# Markdown/MarkdownV2 — both dialects use a single asterisk for bold — so normalise
# it rather than silently rendering the literal '**' in the chat.
_DOUBLE_STAR_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


async def _exec_notify_telegram(db, profile_id: str, params: dict) -> dict:
    """Send to the profile's linked Telegram chat via the Phase 23.c bridge.
    The bridge is best-effort (missing link / muted chat / stopped bot = no-op),
    so report whether a link exists rather than pretending delivery."""
    from app.db import telegram_link_repository
    from app.services import notification_service

    text = _notify_text(params)
    if not text:
        raise ValueError("notify.telegram: 'text' is required")
    parse_mode = str(params.get("parse_mode") or "").strip()
    if parse_mode not in _TELEGRAM_PARSE_MODES:
        raise ValueError(f"notify.telegram: invalid parse_mode {parse_mode!r} (Markdown|MarkdownV2|HTML)")
    if parse_mode in ("Markdown", "MarkdownV2"):
        text = _DOUBLE_STAR_BOLD_RE.sub(r"*\1*", text)
    link = await telegram_link_repository.get_by_profile_id(db, profile_id)
    if link is None:
        raise RuntimeError("notify.telegram: no Telegram chat linked to this profile")
    await notification_service.notify_telegram(db, profile_id, "workflow", text, parse_mode=parse_mode or None)
    return {"queued": True, "channel": "telegram", "parse_mode": parse_mode or None}


# ── Phase 52 (roadmap fase 20) — Telegram channel helpers + send nodes ───────

def _telegram_bot():
    """The live bot Application, or None when the bot isn't running. Kept behind a
    helper so every telegram.* node degrades to a clean no-op off Telegram."""
    from app.telegram import bot as telegram_bot

    app = telegram_bot.get_bot()
    return app.bot if app is not None else None


def _resolve_chat_id(params: dict, ctx: dict):
    """A telegram.* node targets an explicit ``chat_id`` (expression, already
    resolved) or, failing that, the chat the run originated from (a ``telegram`` /
    ``chat`` trigger puts ``chat_id`` on ``$trigger``)."""
    chat_id = params.get("chat_id")
    if chat_id in (None, ""):
        trigger = ctx.get("trigger") if isinstance(ctx.get("trigger"), dict) else {}
        chat_id = trigger.get("chat_id")
    if chat_id in (None, ""):
        raise ValueError("telegram: no 'chat_id' given and the run has no originating chat")
    return chat_id


def _telegram_parse_mode(params: dict) -> str | None:
    parse_mode = str(params.get("parse_mode") or "").strip()
    if parse_mode not in _TELEGRAM_PARSE_MODES:
        raise ValueError(f"telegram: invalid parse_mode {parse_mode!r} (Markdown|MarkdownV2|HTML)")
    return parse_mode or None


async def _exec_telegram_send(params: dict, node_input, ctx: dict) -> dict:
    """20.2 — send text to any chat/thread. Off Telegram it no-ops (``sent:
    False``), mirroring the silent-drop of the notify bridge; a send that raises
    (e.g. a chat the bot doesn't own) surfaces so On error applies."""
    chat_id = _resolve_chat_id(params, ctx)
    text = params.get("text")
    if text in (None, ""):
        text = node_input
    if not isinstance(text, str):
        text = json.dumps(text, default=str, ensure_ascii=False)
    bot = _telegram_bot()
    if bot is None:
        return {"sent": False, "reason": "bot_not_running", "chat_id": chat_id}
    kwargs: dict = {"chat_id": chat_id, "text": text, "parse_mode": _telegram_parse_mode(params)}
    if params.get("thread_id") not in (None, ""):
        kwargs["message_thread_id"] = int(params["thread_id"])
    if params.get("reply_to") not in (None, ""):
        kwargs["reply_to_message_id"] = int(params["reply_to"])
    if _as_bool(params.get("disable_preview")):
        kwargs["disable_web_page_preview"] = True
    try:
        msg = await bot.send_message(**kwargs)
    except Exception as exc:  # noqa: BLE001 — surface as a node failure (retry/onError)
        raise RuntimeError(f"telegram.send: {exc}") from exc
    return {"sent": True, "message_id": msg.message_id, "chat_id": msg.chat_id}


async def _exec_telegram_send_media(params: dict, ctx: dict) -> dict:
    """20.2 — send a photo/document from workspace storage or a URL, with caption."""
    chat_id = _resolve_chat_id(params, ctx)
    kind = str(params.get("media_type") or "document").strip().lower()
    source = params.get("url") or params.get("path")
    if not source:
        raise ValueError("telegram.sendMedia: 'url' or 'path' is required")
    caption = params.get("caption")
    bot = _telegram_bot()
    if bot is None:
        return {"sent": False, "reason": "bot_not_running", "chat_id": chat_id}
    # A path is confined to the workspace storage (fase 4.2); a URL is passed through.
    media = str(source)
    fh = None
    if not media.startswith(("http://", "https://")):
        fh = open(_safe_workspace_path(media), "rb")  # noqa: SIM115 — closed below
        media = fh
    senders = {
        "photo": ("send_photo", "photo"), "document": ("send_document", "document"),
        "audio": ("send_audio", "audio"), "voice": ("send_voice", "voice"),
        "video": ("send_video", "video"),
    }
    method_name, arg = senders.get(kind, senders["document"])
    try:
        msg = await getattr(bot, method_name)(chat_id=chat_id, caption=caption, **{arg: media})
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"telegram.sendMedia: {exc}") from exc
    finally:
        if fh is not None:
            fh.close()
    return {"sent": True, "message_id": msg.message_id, "chat_id": msg.chat_id, "media_type": kind}


async def _exec_telegram_edit(params: dict, ctx: dict) -> dict:
    """20.2 — edit a message sent earlier in the run (progress bars, done edits)."""
    chat_id = _resolve_chat_id(params, ctx)
    message_id = params.get("message_id")
    if message_id in (None, ""):
        raise ValueError("telegram.editMessage: 'message_id' is required")
    text = params.get("text")
    if text in (None, ""):
        raise ValueError("telegram.editMessage: 'text' is required")
    bot = _telegram_bot()
    if bot is None:
        return {"edited": False, "reason": "bot_not_running", "chat_id": chat_id}
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=int(message_id), text=str(text),
            parse_mode=_telegram_parse_mode(params),
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"telegram.editMessage: {exc}") from exc
    return {"edited": True, "message_id": int(message_id), "chat_id": chat_id}


async def _exec_telegram_delete(params: dict, ctx: dict) -> dict:
    """20.2 — remove a message sent earlier in the run."""
    chat_id = _resolve_chat_id(params, ctx)
    message_id = params.get("message_id")
    if message_id in (None, ""):
        raise ValueError("telegram.deleteMessage: 'message_id' is required")
    bot = _telegram_bot()
    if bot is None:
        return {"deleted": False, "reason": "bot_not_running", "chat_id": chat_id}
    try:
        await bot.delete_message(chat_id=chat_id, message_id=int(message_id))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"telegram.deleteMessage: {exc}") from exc
    return {"deleted": True, "message_id": int(message_id), "chat_id": chat_id}


async def _exec_notify_email(params: dict) -> dict:
    from app.services import email_service

    to = params.get("to") or ""
    subject = str(params.get("subject") or "SpiceSibyl workflow notification")
    body = _notify_text(params)
    return await email_service.send_email(to, subject, body)


async def _exec_notify_webhook(params: dict, node_input) -> dict:
    """POST a JSON payload to an external webhook (Slack/Discord/ntfy/…)."""
    payload = params.get("payload")
    if payload is None:
        payload = node_input
    out = await _exec_http_request({
        "method": str(params.get("method") or "POST"),
        "url": params.get("url"),
        "headers": params.get("headers"),
        "body": payload,
        "timeout": params.get("timeout"),
    })
    return {"sent": True, "status": out["status"], "response": out["json"] if out["json"] is not None else out["text"]}


async def _exec_notify_inapp(db, profile_id: str, params: dict) -> dict:
    """Push a notification to the web UI bell (persisted + live SSE)."""
    from app.services import notification_service

    title = str(params.get("title") or "Workflow")
    body = _notify_text(params)
    await notification_service.notify_web(db, profile_id, "workflow", title, body)
    return {"queued": True, "channel": "inapp", "title": title}


# ── handlers ─────────────────────────────────────────────────────────────────

@node("kb.search")
async def _h_kb_search(c: DispatchCtx):
    return await _exec_kb_search(c.db, c.profile_id, c.params, c.node_input), ["main"]


@node("chat.reply")
async def _h_chat_reply(c: DispatchCtx):
    # Fase 9.3 — terminal reply node: its text is the conversation answer.
    text = c.params.get("text")
    if text in (None, ""):
        text = c.node_input
    if not isinstance(text, str):
        text = json.dumps(text, default=str, ensure_ascii=False)
    return {"reply": text}, ["main"]


@node("notify.telegram")
async def _h_notify_telegram(c: DispatchCtx):
    return await _exec_notify_telegram(c.db, c.profile_id, c.params), ["main"]


@node("notify.email")
async def _h_notify_email(c: DispatchCtx):
    return await _exec_notify_email(c.params), ["main"]


@node("notify.webhook")
async def _h_notify_webhook(c: DispatchCtx):
    return await _exec_notify_webhook(c.params, c.node_input), ["main"]


@node("notify.inapp")
async def _h_notify_inapp(c: DispatchCtx):
    return await _exec_notify_inapp(c.db, c.profile_id, c.params), ["main"]


@node("telegram.send")
async def _h_telegram_send(c: DispatchCtx):
    return await _exec_telegram_send(c.params, c.node_input, c.ctx), ["main"]


@node("telegram.sendMedia")
async def _h_telegram_send_media(c: DispatchCtx):
    return await _exec_telegram_send_media(c.params, c.ctx), ["main"]


@node("telegram.editMessage")
async def _h_telegram_edit(c: DispatchCtx):
    return await _exec_telegram_edit(c.params, c.ctx), ["main"]


@node("telegram.deleteMessage")
async def _h_telegram_delete(c: DispatchCtx):
    return await _exec_telegram_delete(c.params, c.ctx), ["main"]
