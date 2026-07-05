"""
Phase 23.a — shared Telegram ↔ web conversation history.

Exercises the persistence layer that lets linked Telegram chats store their
exchanges as regular profile conversations: the new ``conversations.channel``
column and the per-chat active-conversation pointer in ``telegram_prefs``.
"""

import asyncio

import aiosqlite

from app.core.config import settings
from app.db import conversation_repository as conv_repo
from app.db import telegram_prefs_repository as prefs_repo
from app.schemas.chat import ChatMessage


async def _connect() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    return db


def test_conversation_channel_roundtrips(_init_database):
    async def run():
        db = await _connect()
        try:
            tg = await conv_repo.create_conversation(
                db, "from telegram", "gpt", "p23-a", channel="telegram"
            )
            web = await conv_repo.create_conversation(db, "from web", "gpt", "p23-a")
            assert tg.channel == "telegram"
            assert web.channel == "web"  # default

            # channel survives round-trips through both read paths
            full = await conv_repo.get_conversation(db, tg.id)
            assert full is not None and full.channel == "telegram"

            summaries = {c.id: c for c in await conv_repo.list_conversations(db, "p23-a")}
            assert summaries[tg.id].channel == "telegram"
            assert summaries[web.id].channel == "web"
        finally:
            await db.close()

    asyncio.run(run())


def test_active_conversation_pointer(_init_database):
    async def run():
        db = await _connect()
        try:
            conv = await conv_repo.create_conversation(
                db, "active", "gpt", "p23-b", channel="telegram"
            )
            await conv_repo.append_messages(
                db,
                conv.id,
                [
                    ChatMessage(role="user", content="ciao"),
                    ChatMessage(role="assistant", content="salve", model="gpt"),
                ],
            )
        finally:
            await db.close()

        chat_id = 987654
        await prefs_repo.set_active_conversation(chat_id, conv.id)
        assert (await prefs_repo.load_all_active()).get(chat_id) == conv.id

        # the persisted exchange is retrievable for hydration
        db = await _connect()
        try:
            hydrated = await conv_repo.get_conversation(db, conv.id)
        finally:
            await db.close()
        roles = [m.role for m in hydrated.messages]
        assert roles == ["user", "assistant"]

        # clearing the pointer (/new) drops it from the warm-start map
        await prefs_repo.set_active_conversation(chat_id, None)
        assert chat_id not in await prefs_repo.load_all_active()

    asyncio.run(run())


# ── Phase 23.b — MCP/tool loop from Telegram ─────────────────────────────────


def test_tools_toggle_persists(_init_database):
    """The per-chat /tools toggle round-trips through telegram_prefs."""
    async def run():
        chat_id = 424242
        # default (never set) is absent from the warm-start map → OFF
        assert chat_id not in await prefs_repo.load_all_tools()

        await prefs_repo.set_tools(chat_id, True)
        assert (await prefs_repo.load_all_tools()).get(chat_id) is True

        await prefs_repo.set_tools(chat_id, False)
        assert (await prefs_repo.load_all_tools()).get(chat_id) is False

    asyncio.run(run())


def test_assemble_tool_defs_includes_builtins(_init_database):
    """The bot's tool assembly always exposes the built-in tools (MCP optional)."""
    async def run():
        from app.telegram import bot

        defs = await bot._assemble_tool_defs(profile_id=None)
        names = {(d.get("function") or {}).get("name") for d in defs}
        # a couple of always-present built-ins from the registry
        assert "calculator" in names
        assert "web_search" in names

    asyncio.run(run())
