"""
reminder_service — cross-channel reminders (Phase 23.d).

Owns the whole reminder lifecycle: creation (from a raw `/remind`-style string
or from structured web-form fields), a channel-agnostic polling loop that
fires due reminders regardless of whether the Telegram bot is running,
recurrence rescheduling, smart-reminder execution (a small bounded tool loop,
not a full agent run — see app.services.workflow_service for that), and
snooze/repeat actions shared by both the Telegram inline keyboard and the web
toast.
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.db import reminder_repository as reminder_repo
from app.services import reminder_parsing

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 20
_SMART_TOOL_NAMES = frozenset({"fetch_rss", "get_weather", "kb_search", "search_conversations"})
_SMART_MAX_STEPS = 4
_SMART_SYSTEM_PROMPT = (
    "A scheduled reminder has just fired. The user set it up in advance and is not "
    "present to answer questions now — you must deliver the reminder's message "
    "immediately, never ask for clarification and never treat this as the start of "
    "a conversation. The text below is exactly what the user asked to be reminded "
    "about when they scheduled it. Only call a tool if it genuinely needs live "
    "external information (news, weather, feeds, past conversations); for a plain "
    "note or instruction (e.g. \"drink water\"), just restate it directly. "
    "Reply with ONLY the final reminder message to send — no questions, no "
    "meta-commentary about the reminder itself, and no further tool calls."
)

# Model used to parse a `/remind` phrase when reminder_parsing's regex/NL rules
# don't recognize it — a fallback for phrasing (any language, typos, synonyms
# like "fra" vs "tra") the hand-written rules can't anticipate.
_PARSE_FALLBACK_MODEL = "nvidia/moonshotai/kimi-k2.6"

_poll_task: asyncio.Task | None = None


def _resolve_tz(tz_name: str | None) -> ZoneInfo:
    name = tz_name or settings.timezone
    try:
        return ZoneInfo(name)
    except Exception:
        logger.warning("reminder_service: invalid timezone %r, falling back to UTC", name)
        return ZoneInfo("UTC")


# ── Creation ──────────────────────────────────────────────────────────────────

async def create_from_text(
    raw: str,
    *,
    owner_profile_id: str | None,
    chat_id: int | None,
    channels: str,
    tz_name: str | None = None,
    smart: bool = False,
) -> dict | None:
    """Parse a `/remind <when> <text>`-style raw string and persist it.

    Returns {"id", "recurrence", "fire_at", "text"} or None if the time
    expression wasn't recognized.
    """
    tz = _resolve_tz(tz_name)
    parsed = reminder_parsing.parse_recurrence_and_when(raw, tz)
    if parsed is None:
        parsed = await _llm_parse_fallback(raw, tz)
    if parsed is None:
        return None
    recurrence, fire_at, text = parsed
    reminder_id = await reminder_repo.create(
        owner_profile_id=owner_profile_id,
        chat_id=chat_id,
        text=None if smart else text,
        smart_prompt=text if smart else None,
        recurrence=recurrence,
        fire_at=fire_at,
        timezone=tz_name,
        channels=channels,
    )
    return {"id": reminder_id, "recurrence": recurrence, "fire_at": fire_at, "text": text}


async def _llm_parse_fallback(raw: str, tz: ZoneInfo) -> tuple[str, int, str] | None:
    """LLM fallback for a `/remind` phrase reminder_parsing's rules didn't recognize.

    Best-effort: any failure (provider error, bad JSON, missing fields) returns
    None so the caller falls through to the same "invalid time" error as before.
    """
    from app.schemas.chat import ChatCompletionRequest, ChatMessage
    from app.services.provider_factory import ProviderFactory

    now = datetime.now(tz)
    prompt = (
        f"Current date/time: {now.isoformat()}.\n"
        f"Reminder command input: \"{raw}\"\n\n"
        "This is a '<when> <text>' reminder request, in any language or phrasing. Extract:\n"
        "- recurrence: one of \"once\", \"daily\", \"weekly:<comma-separated 3-letter weekdays, "
        "e.g. mon,wed>\", or \"cron:<min>,<hour>,<day-of-month>,<month>,<day-of-week>\"\n"
        "- fire_at: the next occurrence, as an ISO 8601 datetime with UTC offset\n"
        "- text: the reminder text/prompt, with the time expression removed\n\n"
        "Reply with ONLY a JSON object: {\"recurrence\": ..., \"fire_at\": ..., \"text\": ...}. "
        "No markdown fences, no explanation."
    )
    provider = ProviderFactory.get_provider(_PARSE_FALLBACK_MODEL)
    request = ChatCompletionRequest(
        model=_PARSE_FALLBACK_MODEL,
        messages=[ChatMessage(role="user", content=prompt)],
        stream=False,
        profile_id="default",
    )
    try:
        response = await provider.complete(request)
    except Exception:
        logger.exception("reminder_service: LLM parse fallback call failed")
        return None
    if hasattr(response, "model_dump"):
        response = response.model_dump()
    choices = response.get("choices") or []
    if not choices:
        return None
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if content.startswith("```"):
        content = content.strip("`")
        content = content.split("\n", 1)[-1] if "\n" in content else content

    try:
        data = json.loads(content)
        recurrence = str(data["recurrence"])
        fire_dt = datetime.fromisoformat(str(data["fire_at"]))
        text = str(data["text"]).strip()
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        logger.warning("reminder_service: LLM parse fallback returned unparseable output: %r", content)
        return None

    if fire_dt.tzinfo is None:
        fire_dt = fire_dt.replace(tzinfo=tz)
    if not text:
        return None
    return recurrence, int(fire_dt.timestamp()), text


async def create(
    *,
    owner_profile_id: str | None,
    chat_id: int | None = None,
    text: str | None = None,
    smart_prompt: str | None = None,
    recurrence: str = "once",
    fire_at: int,
    timezone: str | None = None,
    channels: str = "web",
) -> str:
    """Create a reminder from already-structured fields (web UI form)."""
    return await reminder_repo.create(
        owner_profile_id=owner_profile_id, chat_id=chat_id, text=text,
        smart_prompt=smart_prompt, recurrence=recurrence, fire_at=fire_at,
        timezone=timezone, channels=channels,
    )


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def list_for_profile(profile_id: str):
    return await reminder_repo.list_for_profile(profile_id)


async def get(reminder_id: str):
    return await reminder_repo.get(reminder_id)


async def edit(reminder_id: str, **fields) -> None:
    await reminder_repo.update(reminder_id, **fields)


async def delete(reminder_id: str, *, owner_profile_id: str | None = None, chat_id: int | None = None) -> bool:
    return await reminder_repo.delete(reminder_id, owner_profile_id=owner_profile_id, chat_id=chat_id)


# ── Snooze / repeat ────────────────────────────────────────────────────────────

async def snooze(reminder_id: str, minutes: int = 10) -> bool:
    row = await reminder_repo.get(reminder_id)
    if not row:
        return False
    new_fire_at = int(time.time()) + minutes * 60
    await reminder_repo.update(reminder_id, fire_at=new_fire_at, fired=0, active=1)
    return True


async def repeat(reminder_id: str) -> bool:
    """Re-deliver the reminder's content immediately, without touching its schedule."""
    row = await reminder_repo.get(reminder_id)
    if not row:
        return False
    text = await _resolve_text(row)
    await _deliver(row, text)
    return True


# ── Firing ────────────────────────────────────────────────────────────────────

async def _resolve_text(row) -> str:
    if row["smart_prompt"]:
        return await _run_smart_prompt(row["smart_prompt"], row["owner_profile_id"] or "default")
    return row["text"] or ""


async def _run_smart_prompt(prompt: str, profile_id: str) -> str:
    """Bounded tool loop for a smart reminder — not a persisted agent run."""
    from app.schemas.chat import ChatCompletionRequest, ChatMessage, ToolCall, ToolCallFunction
    from app.services.provider_factory import ProviderFactory
    from app.tools.registry import TOOL_DEFINITIONS, execute_tool

    model = settings.telegram_default_model or settings.default_model
    tools = [t for t in TOOL_DEFINITIONS if t["function"]["name"] in _SMART_TOOL_NAMES]
    messages = [
        ChatMessage(role="system", content=_SMART_SYSTEM_PROMPT),
        ChatMessage(role="user", content=prompt),
    ]
    provider = ProviderFactory.get_provider(model)

    for _ in range(_SMART_MAX_STEPS):
        request = ChatCompletionRequest(
            model=model, messages=messages, tools=tools or None,
            stream=False, profile_id=profile_id,
        )
        try:
            response = await provider.complete(request)
        except Exception:
            logger.exception("reminder_service: smart prompt completion failed")
            return "Error: could not generate the reminder content."
        if hasattr(response, "model_dump"):
            response = response.model_dump()
        choices = response.get("choices") or []
        if not choices:
            return "Error: no response from the model."
        choice = choices[0]
        msg = choice.get("message") or {}
        tool_calls_raw = msg.get("tool_calls") or []
        content = msg.get("content") or ""

        if choice.get("finish_reason") != "tool_calls" or not tool_calls_raw:
            return content

        tool_calls = [
            ToolCall(
                id=tc["id"], type=tc.get("type", "function"),
                function=ToolCallFunction(name=tc["function"]["name"], arguments=tc["function"]["arguments"]),
            )
            for tc in tool_calls_raw
        ]
        messages.append(ChatMessage(role="assistant", content=msg.get("content"), tool_calls=tool_calls))
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            try:
                result = await execute_tool(tc.function.name, args, profile_id=profile_id)
            except (RuntimeError, ValueError, OSError) as exc:
                result = f"Error: {exc}"
            messages.append(ChatMessage(role="tool", tool_call_id=tc.id, content=result))

    return "Reminder step limit reached without a final answer."


async def _deliver(row, text: str) -> None:
    channels = row["channels"]
    reminder_id = row["id"]

    if channels in ("telegram", "both") and row["chat_id"]:
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            from app.telegram import bot as telegram_bot

            bot = telegram_bot.get_bot()
            if bot is not None:
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("💤 +10m", callback_data=f"remind_snooze:{reminder_id}"),
                    InlineKeyboardButton("🔁", callback_data=f"remind_repeat:{reminder_id}"),
                    InlineKeyboardButton("🗑", callback_data=f"remind_delete:{reminder_id}"),
                ]])
                await bot.send_message(chat_id=row["chat_id"], text=f"⏰ {text}", reply_markup=keyboard)
        except Exception:
            logger.exception("reminder_service: telegram delivery failed id=%s", reminder_id)

    if channels in ("web", "both") and row["owner_profile_id"]:
        try:
            from app.db.database import get_db
            from app.services import notification_service

            async for db in get_db():
                await notification_service.notify_web(
                    db, row["owner_profile_id"], "reminderFired",
                    title="⏰ Reminder", body=text,
                    meta={"reminderId": reminder_id, "recurrence": row["recurrence"]},
                )
        except Exception:
            logger.exception("reminder_service: web delivery failed id=%s", reminder_id)


async def fire(reminder_id: str) -> None:
    """Deliver a due reminder and reschedule it (or mark it fired if one-shot)."""
    row = await reminder_repo.get(reminder_id)
    if not row or not row["active"] or row["fired"]:
        return

    text = await _resolve_text(row)
    await _deliver(row, text)

    now = int(time.time())
    tz = _resolve_tz(row["timezone"])
    next_fire = reminder_parsing.compute_next_fire(row["recurrence"], row["fire_at"], tz)
    if next_fire is None:
        await reminder_repo.update(reminder_id, fired=1, last_fired_at=now)
    else:
        await reminder_repo.update(reminder_id, fire_at=next_fire, fired=0, last_fired_at=now)


# ── Polling loop ──────────────────────────────────────────────────────────────

async def _poll_loop() -> None:
    while True:
        try:
            due = await reminder_repo.list_all_active_due(int(time.time()))
            for row in due:
                try:
                    await fire(row["id"])
                except Exception:
                    logger.exception("reminder_service: firing failed id=%s", row["id"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("reminder_service: poll loop iteration failed")
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


def start() -> None:
    global _poll_task
    if _poll_task is not None and not _poll_task.done():
        return
    _poll_task = asyncio.get_running_loop().create_task(_poll_loop())
    logger.info("reminder_service: polling loop started (interval=%ss)", _POLL_INTERVAL_SECONDS)


async def stop() -> None:
    global _poll_task
    if _poll_task is None:
        return
    _poll_task.cancel()
    try:
        await _poll_task
    except asyncio.CancelledError:
        pass
    _poll_task = None
    logger.info("reminder_service: polling loop stopped")
