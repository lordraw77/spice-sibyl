"""Per-chat toggles: /memory, /rag, /notify, /tools and the /tool inspector.

Extracted from the former single-file bot.py.
"""

import logging

import aiosqlite
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.core.config import settings
from app.db import telegram_prefs_repository as prefs_repo
from app.telegram.i18n import t

from .account import _linked_profile_id
from .state import _is_allowed, _locale, _memory_prefs, _notify_prefs, _rag_prefs, _tools_prefs
from .streaming import _assemble_tool_defs

logger = logging.getLogger(__name__)

async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Phase 19: /memory on|off|list|del <id> — personal memory over the linked profile."""
    user = update.effective_user
    if not _is_allowed(user.id):
        return
    chat_id = update.effective_chat.id
    loc = _locale(chat_id)
    args = [a.strip() for a in (context.args or []) if a.strip()]
    sub = args[0].lower() if args else ""

    if sub in ("on", "off"):
        enabled = sub == "on"
        _memory_prefs[chat_id] = enabled
        await prefs_repo.set_memory(chat_id, enabled)
        await update.message.reply_text(t(loc, "memory_on" if enabled else "memory_off"))
        return

    if sub in ("list", "del"):
        profile_id = await _linked_profile_id(user.id)
        if not profile_id:
            await update.message.reply_text(t(loc, "memory_not_linked"))
            return
        from app.db import memory_repository
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            if sub == "list":
                memories = await memory_repository.list_memories(db, profile_id)
                if not memories:
                    await update.message.reply_text(t(loc, "memory_empty"))
                    return
                lines = [
                    f"<code>{m.id[:8]}</code> [{m.category}] {m.content}"
                    + ("" if m.enabled else " (off)")
                    for m in memories[:30]
                ]
                await update.message.reply_text(
                    t(loc, "memory_header") + "\n".join(lines), parse_mode=ParseMode.HTML
                )
                return
            # del <id>
            if len(args) < 2:
                await update.message.reply_text(t(loc, "memory_usage"), parse_mode=ParseMode.HTML)
                return
            prefix = args[1]
            memories = await memory_repository.list_memories(db, profile_id)
            match = next((m for m in memories if m.id.startswith(prefix)), None)
            if not match:
                await update.message.reply_text(t(loc, "memory_not_found"))
                return
            await memory_repository.delete_memory(db, match.id)
        await update.message.reply_text(t(loc, "memory_deleted"))
        return

    await update.message.reply_text(t(loc, "memory_usage"), parse_mode=ParseMode.HTML)


async def cmd_rag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Phase 21: /rag on|off — toggle knowledge-base injection for this chat."""
    user = update.effective_user
    if not _is_allowed(user.id):
        return
    chat_id = update.effective_chat.id
    loc = _locale(chat_id)
    args = [a.strip() for a in (context.args or []) if a.strip()]
    sub = args[0].lower() if args else ""

    if sub in ("on", "off"):
        enabled = sub == "on"
        if enabled and not await _linked_profile_id(user.id):
            await update.message.reply_text(t(loc, "rag_not_linked"))
            return
        _rag_prefs[chat_id] = enabled
        await prefs_repo.set_rag(chat_id, enabled)
        await update.message.reply_text(t(loc, "rag_on" if enabled else "rag_off"))
        return

    await update.message.reply_text(t(loc, "rag_usage"), parse_mode=ParseMode.HTML)


async def cmd_notify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Phase 23.c: /notify on|off — mute/unmute web→Telegram cross-channel pushes."""
    user = update.effective_user
    if not _is_allowed(user.id):
        return
    chat_id = update.effective_chat.id
    loc = _locale(chat_id)
    args = [a.strip() for a in (context.args or []) if a.strip()]
    sub = args[0].lower() if args else ""

    if sub in ("on", "off"):
        enabled = sub == "on"
        _notify_prefs[chat_id] = enabled
        await prefs_repo.set_notify(chat_id, enabled)
        await update.message.reply_text(t(loc, "notify_on" if enabled else "notify_off"))
        return

    await update.message.reply_text(t(loc, "notify_usage"), parse_mode=ParseMode.HTML)


def _split_message(text: str, max_length: int = 4000) -> list[str]:
    """Split text into chunks that respect HTML tags and line breaks."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    current_chunk = ""
    lines = text.split("\n")

    for line in lines:
        if len(current_chunk) + len(line) + 1 <= max_length:
            current_chunk += line + "\n"
        else:
            if current_chunk:
                chunks.append(current_chunk.rstrip("\n"))
            current_chunk = line + "\n"

    if current_chunk:
        chunks.append(current_chunk.rstrip("\n"))

    return chunks


def _format_tool_list(defs: list[dict]) -> str:
    """Render tool definitions as grouped <code> lines for the /tools message."""
    builtins, mcp_tools, custom = [], [], []
    for d in defs:
        name = (d.get("function") or {}).get("name", "?")
        if name.startswith("mcp__"):
            mcp_tools.append(name)
        elif name.startswith("custom__"):
            custom.append(name.removeprefix("custom__"))
        else:
            builtins.append(name)
    lines: list[str] = []
    for label, names in (("🧩 built-in", builtins), ("🔌 MCP", mcp_tools), ("🛠 custom", custom)):
        if names:
            lines.append(f"<b>{label}</b>")
            lines.extend(f"  <code>{n}</code>" for n in names)
    return "\n".join(lines)


async def cmd_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Phase 23.c: /tool on|off — toggle the tool loop for this chat.

    This is the only way to flip the per-chat toggle; /tools is view-only.
    """
    user = update.effective_user
    if not _is_allowed(user.id):
        return
    chat_id = update.effective_chat.id
    loc = _locale(chat_id)
    args = [a.strip() for a in (context.args or []) if a.strip()]
    sub = args[0].lower() if args else ""

    if sub in ("on", "off"):
        enabled = sub == "on"
        _tools_prefs[chat_id] = enabled
        await prefs_repo.set_tools(chat_id, enabled)
        await update.message.reply_text(t(loc, "tools_on" if enabled else "tools_off"))
        return

    await update.message.reply_text(t(loc, "tool_usage"), parse_mode=ParseMode.HTML)


async def cmd_tools(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Phase 23.b: /tools — list the tools available in this chat (view-only).

    Use /tool on|off to toggle the tool loop; /tools never mutates state.
    """
    user = update.effective_user
    if not _is_allowed(user.id):
        return
    chat_id = update.effective_chat.id
    loc = _locale(chat_id)

    profile = await _linked_profile_id(user.id)
    defs = await _assemble_tool_defs(profile, force_refresh=True)
    current = _tools_prefs.get(chat_id, False)

    if defs:
        header = t(loc, "tools_header", count=len(defs))
    else:
        header = t(loc, "tools_none")
    header += "\n\n" + t(loc, "tools_status_on" if current else "tools_status_off")
    await update.message.reply_text(header, parse_mode=ParseMode.HTML)

    # Send tool list in chunks (Telegram limit: 4096 chars)
    if defs:
        tool_list = _format_tool_list(defs)
        chunks = _split_message(tool_list, max_length=4000)
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
