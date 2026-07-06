"""
reminder_parsing — shared time/recurrence parsing for reminders (Phase 23.d).

Single source of truth for turning a `/remind …` command or a `create_reminder`
tool call into (recurrence, first fire_at, text). Replaces the two duplicated
`_parse_when` implementations that used to live in app.telegram.bot and
app.tools.extras.

Recurrence is stored as a compact string, not literal RFC5545 RRULE text:
  * "once"                — one-shot
  * "daily"                — every day at the same local time
  * "weekly:mon,wed"       — every listed weekday, same local time
  * "cron:<min>,<hour>,<dom>,<mon>,<dow>" — power-user 5-field cron, comma
    separated (Telegram command args are whitespace-split, so a literal
    space-separated cron expression can't survive as one token)

Natural-language coverage is deliberately small: pattern matching for the
phrasings named in the roadmap (IT + EN), not a general date-NLP engine.
"""

import re
from datetime import datetime, timedelta

from croniter import croniter

_REL_RE = re.compile(r"^\+?(\d+)\s*([mhd])$", re.IGNORECASE)
_ABS_HM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_ABS_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})$")
_REL_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}

_WEEKDAY_ALIASES = {
    "mon": "mon", "monday": "mon", "lun": "mon", "lunedi": "mon", "lunedì": "mon",
    "tue": "tue", "tuesday": "tue", "mar": "tue", "martedi": "tue", "martedì": "tue",
    "wed": "wed", "wednesday": "wed", "mer": "wed", "mercoledi": "wed", "mercoledì": "wed",
    "thu": "thu", "thursday": "thu", "gio": "thu", "giovedi": "thu", "giovedì": "thu",
    "fri": "fri", "friday": "fri", "ven": "fri", "venerdi": "fri", "venerdì": "fri",
    "sat": "sat", "saturday": "sat", "sab": "sat", "sabato": "sat",
    "sun": "sun", "sunday": "sun", "dom": "sun", "domenica": "sun",
}
_WEEKDAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

_NUMBER_WORDS = {
    "un": 1, "uno": 1, "una": 1, "one": 1,
    "due": 2, "two": 2,
    "tre": 3, "three": 3,
    "quattro": 4, "four": 4,
    "cinque": 5, "five": 5,
    "sei": 6, "six": 6,
    "sette": 7, "seven": 7,
    "otto": 8, "eight": 8,
    "nove": 9, "nine": 9,
    "dieci": 10, "ten": 10,
}

_UNIT_ALIASES = {
    "minuti": "m", "minuto": "m", "minutes": "m", "minute": "m", "min": "m",
    "ore": "h", "ora": "h", "hours": "h", "hour": "h",
    "giorni": "d", "giorno": "d", "days": "d", "day": "d",
}


def _amount(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token.lower())


def _next_weekday_at(now: datetime, target: str, hour: int, minute: int) -> datetime:
    target_idx = _WEEKDAY_INDEX[target]
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (target_idx - now.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def parse_recurrence_and_when(raw: str, tz) -> tuple[str, int, str] | None:
    """Parse a `/remind`-style raw string into (recurrence, fire_at, text).

    Returns None if no recognizable time expression is found.
    """
    raw = raw.strip()
    now = datetime.now(tz)

    # cron:<min>,<hour>,<dom>,<mon>,<dow> <text>
    m = re.match(r"^cron:(\S+)\s+(.+)$", raw, re.IGNORECASE)
    if m:
        expr = m.group(1).replace(",", " ")
        try:
            nxt = croniter(expr, now).get_next(datetime)
        except (ValueError, KeyError):
            return None
        return f"cron:{m.group(1)}", int(nxt.timestamp()), m.group(2).strip()

    # every day HH:MM <text>  (+ "ogni giorno")
    m = re.match(r"^(?:every|ogni)\s+(?:day|giorno)\s+(\d{1,2}):(\d{2})\s+(.+)$", raw, re.IGNORECASE)
    if m:
        hour, minute, text = int(m.group(1)), int(m.group(2)), m.group(3)
        if hour > 23 or minute > 59:
            return None
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return "daily", int(target.timestamp()), text.strip()

    # every <weekday> [HH:MM] <text>
    m = re.match(
        r"^(?:every|ogni)\s+(\w+)(?:\s+(\d{1,2}):(\d{2}))?\s+(.+)$", raw, re.IGNORECASE
    )
    if m and m.group(1).lower() in _WEEKDAY_ALIASES:
        target_day = _WEEKDAY_ALIASES[m.group(1).lower()]
        hour = int(m.group(2)) if m.group(2) else 9
        minute = int(m.group(3)) if m.group(3) else 0
        if hour > 23 or minute > 59:
            return None
        target = _next_weekday_at(now, target_day, hour, minute)
        return f"weekly:{target_day}", int(target.timestamp()), m.group(4).strip()

    # domani/tomorrow [alle|at HH[:MM]] <text>
    m = re.match(
        r"^(domani|tomorrow)(?:\s+(?:alle|at)\s+(\d{1,2})(?::(\d{2}))?)?\s+(.+)$", raw, re.IGNORECASE
    )
    if m:
        hour = int(m.group(2)) if m.group(2) else 9
        minute = int(m.group(3)) if m.group(3) else 0
        if hour > 23 or minute > 59:
            return None
        target = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return "once", int(target.timestamp()), m.group(4).strip()

    # dopodomani/day after tomorrow [alle|at HH[:MM]] <text>
    m = re.match(
        r"^(dopodomani|day after tomorrow)(?:\s+(?:alle|at)\s+(\d{1,2})(?::(\d{2}))?)?\s+(.+)$",
        raw, re.IGNORECASE,
    )
    if m:
        hour = int(m.group(2)) if m.group(2) else 9
        minute = int(m.group(3)) if m.group(3) else 0
        if hour > 23 or minute > 59:
            return None
        target = (now + timedelta(days=2)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return "once", int(target.timestamp()), m.group(4).strip()

    # tra/fra/in <n|word> minuti|ore|giorni <text>
    m = re.match(
        r"^(?:tra|fra|in)\s+(\S+)\s+(\S+)\s+(.+)$", raw, re.IGNORECASE
    )
    if m:
        amount = _amount(m.group(1))
        unit = _UNIT_ALIASES.get(m.group(2).lower())
        if amount is not None and unit is not None:
            seconds = amount * _REL_UNIT_SECONDS[unit]
            target = now + timedelta(seconds=seconds)
            return "once", int(target.timestamp()), m.group(3).strip()

    # il/on the <day> alle/at HH[:MM] <text>  (this month, or next if already past)
    m = re.match(
        r"^(?:il|on the)\s+(\d{1,2})(?:°|st|nd|rd|th)?\s+(?:alle|at)\s+(\d{1,2})(?::(\d{2}))?\s+(.+)$",
        raw, re.IGNORECASE,
    )
    if m:
        day, hour = int(m.group(1)), int(m.group(2))
        minute = int(m.group(3)) if m.group(3) else 0
        if hour > 23 or minute > 59 or day < 1 or day > 31:
            return None
        try:
            target = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            return None
        if target <= now:
            year, month = now.year + (1 if now.month == 12 else 0), (now.month % 12) + 1
            try:
                target = target.replace(year=year, month=month)
            except ValueError:
                return None
        return "once", int(target.timestamp()), m.group(4).strip()

    # stasera/tonight <text> (today 20:00, tomorrow if already past)
    m = re.match(r"^(stasera|tonight)\s+(.+)$", raw, re.IGNORECASE)
    if m:
        target = now.replace(hour=20, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return "once", int(target.timestamp()), m.group(2).strip()

    # <weekday> [alle|at HH[:MM]] <text> — one-shot, next occurrence of that weekday
    m = re.match(r"^(\w+)(?:\s+(?:alle|at)\s+(\d{1,2})(?::(\d{2}))?)?\s+(.+)$", raw, re.IGNORECASE)
    if m and m.group(1).lower() in _WEEKDAY_ALIASES:
        target_day = _WEEKDAY_ALIASES[m.group(1).lower()]
        hour = int(m.group(2)) if m.group(2) else 9
        minute = int(m.group(3)) if m.group(3) else 0
        if hour > 23 or minute > 59:
            return None
        target = _next_weekday_at(now, target_day, hour, minute)
        return "once", int(target.timestamp()), m.group(4).strip()

    # existing single-token forms: +30m / 2h / 1d / HH:MM / YYYY-MM-DD HH:MM
    m = re.match(r"^(\S+)\s+(.+)$", raw)
    if not m:
        return None
    token, text = m.group(1), m.group(2)

    rel = _REL_RE.match(token)
    if rel:
        amount = int(rel.group(1))
        unit = rel.group(2).lower()
        target = now + timedelta(seconds=amount * _REL_UNIT_SECONDS[unit])
        return "once", int(target.timestamp()), text.strip()

    absolute = _ABS_HM_RE.match(token)
    if absolute:
        hour, minute = int(absolute.group(1)), int(absolute.group(2))
        if hour > 23 or minute > 59:
            return None
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return "once", int(target.timestamp()), text.strip()

    full = _ABS_DATE_RE.match(token)
    if full:
        try:
            target = datetime(
                int(full.group(1)), int(full.group(2)), int(full.group(3)),
                int(full.group(4)), int(full.group(5)), tzinfo=tz,
            )
        except ValueError:
            return None
        return "once", int(target.timestamp()), text.strip()

    return None


def compute_next_fire(recurrence: str, after_ts: int, tz) -> int | None:
    """Given the fire_at that just fired, compute the next occurrence (or None for 'once')."""
    if recurrence == "once":
        return None

    fired_at = datetime.fromtimestamp(after_ts, tz)

    if recurrence == "daily":
        return int((fired_at + timedelta(days=1)).timestamp())

    if recurrence.startswith("weekly:"):
        days = [d for d in recurrence[len("weekly:"):].split(",") if d in _WEEKDAY_INDEX]
        if not days:
            return None
        now = datetime.now(tz)
        candidates = [
            _next_weekday_at(now, d, fired_at.hour, fired_at.minute) for d in days
        ]
        return int(min(candidates).timestamp())

    if recurrence.startswith("cron:"):
        expr = recurrence[len("cron:"):].replace(",", " ")
        try:
            nxt = croniter(expr, fired_at).get_next(datetime)
        except (ValueError, KeyError):
            return None
        return int(nxt.timestamp())

    return None
