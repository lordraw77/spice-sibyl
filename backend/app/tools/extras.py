"""
Phase 19 built-in tools — expansion of the tool registry.

  kb_search            — agentic RAG over the profile's knowledge base
  search_conversations — episodic memory via the messages FTS5 index
  generate_image       — expose the image-generation chain as a tool
  get_weather          — current weather + forecast via Open-Meteo (keyless)
  fetch_rss            — latest N entries of an RSS/Atom feed
  create_reminder      — natural-language access to Telegram reminders
  extract_document     — read a PDF/DOCX/TXT/MD from a URL without KB ingestion
  http_request         — generic HTTP call with SSRF hardening

All handlers return plain text (tool-result contract). Failures come back as
"Error: …" strings so the model can recover instead of crashing the loop.
"""

import ipaddress
import json
import logging
import re
import socket
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import urlparse

import aiosqlite
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def _connect() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    return db


# ── SSRF hardening ───────────────────────────────────────────────────────────

def assert_public_url(url: str) -> str | None:
    """Return an error string when the URL must not be fetched, else None.

    Blocks non-HTTP schemes and hosts resolving to private/loopback/link-local
    ranges; honors the optional HTTP_REQUEST_ALLOWED_DOMAINS suffix allowlist.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Error: URL scheme '{parsed.scheme}' not allowed (http/https only)"
    host = parsed.hostname
    if not host:
        return "Error: URL has no host"

    allowlist = [d.strip().lower() for d in
                 (settings.http_request_allowed_domains or "").split(",") if d.strip()]
    if allowlist and not any(host.lower() == d or host.lower().endswith("." + d)
                             for d in allowlist):
        return f"Error: domain '{host}' is not in the configured allowlist"

    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        return f"Error: cannot resolve host '{host}': {exc}"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return f"Error: host '{host}' resolves to a non-public address ({ip}) — blocked"
    return None


# ── kb_search ────────────────────────────────────────────────────────────────

async def kb_search(query: str, top_k: int = 4, profile_id: str = "default") -> str:
    """Agentic RAG: query the profile's knowledge base on demand."""
    from app.services import rag_service

    top_k = max(1, min(int(top_k), 10))
    db = await _connect()
    try:
        await db.execute("PRAGMA foreign_keys=ON")
        sources = await rag_service.retrieve(db, profile_id, query, top_k=top_k)
    except Exception as exc:  # noqa: BLE001 — surface retrieval issues as text
        logger.exception("kb_search failed for profile=%s", profile_id)
        return f"Error: knowledge base search failed: {exc}"
    finally:
        await db.close()

    if not sources:
        return f"No relevant passages found in the knowledge base for: {query}"
    parts = [
        f"[{i + 1}] {s.filename} (chunk {s.chunk_index}, score {s.score:.2f})\n{s.content}"
        for i, s in enumerate(sources)
    ]
    return "\n\n".join(parts)


# ── search_conversations ─────────────────────────────────────────────────────

async def search_conversations(query: str, limit: int = 5, profile_id: str = "default") -> str:
    """Episodic memory: full-text search over past conversations."""
    from app.db import search_repository

    limit = max(1, min(int(limit), 20))
    db = await _connect()
    try:
        results = await search_repository.search_conversations(
            db, query, profile_id=profile_id, limit=limit
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("search_conversations failed for profile=%s", profile_id)
        return f"Error: conversation search failed: {exc}"
    finally:
        await db.close()

    if not results:
        return f"No past conversations match: {query}"
    lines = [
        f"- {r.title} ({datetime.fromtimestamp(r.updated_at).strftime('%Y-%m-%d')}): {r.snippet}"
        for r in results
    ]
    return "Past conversations matching the query:\n" + "\n".join(lines)


# ── generate_image ───────────────────────────────────────────────────────────

async def generate_image(prompt: str) -> str:
    """Generate an image mid-reasoning via the configured provider chain.

    Returns markdown with a base64 data URI; the chat loop shows it to the user
    and feeds the model a short placeholder instead.
    """
    from app.services.image_service import ImageGenerationError
    from app.services.image_service import generate_image as _generate

    try:
        result = await _generate(prompt)
    except ImageGenerationError as exc:
        return f"Error: image generation failed: {exc}"
    provider = result.get("provider", "?")
    model = result.get("model", "?")
    b64 = result.get("b64_json", "")
    if not b64:
        return "Error: image generation returned no data"
    return f"![Generated image — {provider}/{model}](data:image/png;base64,{b64})"


# ── get_weather ──────────────────────────────────────────────────────────────

_WMO_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}


def _wmo(code) -> str:
    try:
        return _WMO_CODES.get(int(code), f"weather code {code}")
    except (TypeError, ValueError):
        return "unknown"


async def get_weather(location: str, days: int = 3) -> str:
    """Current weather + forecast via Open-Meteo (free, no API key)."""
    days = max(1, min(int(days), 7))
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            geo = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1, "language": "en"},
            )
            geo.raise_for_status()
            hits = (geo.json().get("results") or [])
            if not hits:
                return f"Error: location not found: {location}"
            place = hits[0]

            forecast = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                               "precipitation,weather_code,wind_speed_10m",
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                             "precipitation_probability_max",
                    "forecast_days": days,
                    "timezone": "auto",
                },
            )
            forecast.raise_for_status()
            data = forecast.json()
    except (httpx.HTTPError, OSError) as exc:
        return f"Error: weather lookup failed: {exc}"

    name = f"{place.get('name')}, {place.get('country', '')}".strip(", ")
    cur = data.get("current") or {}
    lines = [
        f"Weather for {name}:",
        f"Now: {_wmo(cur.get('weather_code'))}, {cur.get('temperature_2m')}°C "
        f"(feels like {cur.get('apparent_temperature')}°C), "
        f"humidity {cur.get('relative_humidity_2m')}%, wind {cur.get('wind_speed_10m')} km/h",
    ]
    daily = data.get("daily") or {}
    for i, day in enumerate(daily.get("time") or []):
        lines.append(
            f"{day}: {_wmo((daily.get('weather_code') or [None] * 7)[i])}, "
            f"{(daily.get('temperature_2m_min') or ['?'] * 7)[i]}–"
            f"{(daily.get('temperature_2m_max') or ['?'] * 7)[i]}°C, "
            f"precipitation {(daily.get('precipitation_probability_max') or ['?'] * 7)[i]}%"
        )
    return "\n".join(lines)


# ── fetch_rss ────────────────────────────────────────────────────────────────

def _text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


async def fetch_rss(url: str, max_entries: int = 5) -> str:
    """Fetch an RSS 2.0 / Atom feed and return the latest N entries."""
    blocked = assert_public_url(url)
    if blocked:
        return blocked
    max_entries = max(1, min(int(max_entries), 20))
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "SpiceSibyl/1.0"})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
    except (httpx.HTTPError, OSError) as exc:
        return f"Error: cannot fetch feed {url}: {exc}"
    except ET.ParseError as exc:
        return f"Error: invalid XML in feed {url}: {exc}"

    entries: list[str] = []
    # RSS 2.0
    for item in root.findall(".//channel/item")[:max_entries]:
        title = _text(item.find("title"))
        link = _text(item.find("link"))
        date = _text(item.find("pubDate"))
        desc = _text(item.find("description"))[:300]
        entries.append(f"• {title} ({date})\n  {link}\n  {desc}".rstrip())
    # Atom
    if not entries:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns)[:max_entries]:
            title = _text(entry.find("a:title", ns))
            link_el = entry.find("a:link", ns)
            link = link_el.get("href", "") if link_el is not None else ""
            date = _text(entry.find("a:updated", ns)) or _text(entry.find("a:published", ns))
            summary = _text(entry.find("a:summary", ns))[:300]
            entries.append(f"• {title} ({date})\n  {link}\n  {summary}".rstrip())

    if not entries:
        return f"No entries found in feed: {url}"
    feed_title = _text(root.find(".//channel/title")) or _text(
        root.find("{http://www.w3.org/2005/Atom}title")
    )
    header = f"Feed: {feed_title}\n" if feed_title else ""
    return header + "\n".join(entries)


# ── create_reminder ──────────────────────────────────────────────────────────

async def create_reminder(
    text: str, when: str, profile_id: str = "default", recurrence: str | None = None
) -> str:
    """Create a reminder for the profile's linked Telegram account.

    `when` accepts the shared reminder_parsing grammar: relative ('+30m',
    '2h', '1d'), absolute ('HH:MM', 'YYYY-MM-DD HH:MM'), or natural-language
    phrases ('domani alle 9', 'tra due ore', ...). `recurrence`, if given, is
    one of 'daily' or 'weekly:<mon,tue,...>' — the Phase 23.d compact
    recurrence grammar (see app.services.reminder_parsing).
    """
    from app.db import telegram_link_repository
    from app.services import reminder_service

    if not settings.telegram_bot_token:
        return "Error: the Telegram bot is not configured, reminders cannot be delivered"

    db = await _connect()
    try:
        link = await telegram_link_repository.get_by_profile_id(db, profile_id)
    finally:
        await db.close()
    if not link:
        return (
            "Error: this profile is not linked to a Telegram account. "
            "Send /link to the bot and paste the code in the web sidebar first."
        )

    chat_id = link["telegram_id"]
    import zoneinfo

    from app.services import reminder_parsing

    tz = zoneinfo.ZoneInfo(settings.timezone)
    parsed = reminder_parsing.parse_recurrence_and_when(f"{when} {text}", tz)
    if parsed is None:
        return (
            f"Error: cannot parse time '{when}'. Use '+30m', '2h', '1d', "
            "'HH:MM', 'YYYY-MM-DD HH:MM', or a natural-language phrase."
        )
    _, fire_at, parsed_text = parsed
    final_recurrence = recurrence or "once"

    reminder_id = await reminder_service.create(
        owner_profile_id=profile_id, chat_id=chat_id, text=parsed_text,
        recurrence=final_recurrence, fire_at=fire_at, timezone=settings.timezone,
        channels="telegram",
    )
    when_str = datetime.fromtimestamp(fire_at).strftime("%Y-%m-%d %H:%M")
    return f"Reminder set for {when_str} ({settings.timezone}, recurrence={final_recurrence}) [id={reminder_id[:8]}]: {parsed_text}"


# ── extract_document ─────────────────────────────────────────────────────────

async def extract_document(url: str, max_chars: int = 8000) -> str:
    """Download a PDF/DOCX/TXT/MD document from a URL and return its text."""
    blocked = assert_public_url(url)
    if blocked:
        return blocked
    max_chars = max(500, min(int(max_chars), 20000))
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "SpiceSibyl/1.0"})
            resp.raise_for_status()
            data = resp.content
            content_type = (resp.headers.get("content-type") or "").lower()
    except (httpx.HTTPError, OSError) as exc:
        return f"Error: cannot download {url}: {exc}"

    filename = urlparse(url).path.rsplit("/", 1)[-1] or "document"
    if "." not in filename:
        if "pdf" in content_type:
            filename += ".pdf"
        elif "officedocument.wordprocessingml" in content_type:
            filename += ".docx"
        else:
            filename += ".txt"

    from app.services import rag_service

    try:
        text = rag_service.extract_text(filename, data)
    except ValueError:
        # Unsupported extension — best-effort plain-text decode
        text = data.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 — corrupt files must not crash the loop
        return f"Error: cannot extract text from {filename}: {exc}"

    text = text.strip()
    if not text:
        return f"No extractable text found in {filename}"
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[Truncated — {len(text) - max_chars} chars omitted]"
    return f"Content of {filename}:\n\n{text}"


# ── http_request ─────────────────────────────────────────────────────────────

_ALLOWED_METHODS = frozenset({"GET", "POST"})
_MAX_RESPONSE_CHARS = 8000


async def http_request(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    body=None,
    params: dict | None = None,
) -> str:
    """Generic HTTP call (GET/POST) with SSRF hardening and size limits."""
    method = (method or "GET").upper()
    if method not in _ALLOWED_METHODS:
        return f"Error: method '{method}' not allowed (GET/POST only)"
    blocked = assert_public_url(url)
    if blocked:
        return blocked

    safe_headers = {
        str(k): str(v) for k, v in (headers or {}).items()
        if str(k).lower() not in ("host", "content-length")
    }
    safe_headers.setdefault("User-Agent", "SpiceSibyl/1.0")

    kwargs: dict = {"headers": safe_headers, "params": params or None}
    if method == "POST" and body is not None:
        if isinstance(body, (dict, list)):
            kwargs["json"] = body
        else:
            kwargs["content"] = str(body)

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.request(method, url, **kwargs)
    except (httpx.HTTPError, OSError) as exc:
        return f"Error: request to {url} failed: {exc}"

    content_type = (resp.headers.get("content-type") or "").lower()
    if "json" in content_type:
        try:
            text = json.dumps(resp.json(), ensure_ascii=False, indent=2)
        except ValueError:
            text = resp.text
    elif "text" in content_type or "xml" in content_type:
        text = resp.text
    else:
        return f"HTTP {resp.status_code} — binary response ({content_type}), {len(resp.content)} bytes"

    if len(text) > _MAX_RESPONSE_CHARS:
        text = text[:_MAX_RESPONSE_CHARS] + f"\n\n[Truncated — {len(text) - _MAX_RESPONSE_CHARS} chars omitted]"
    return f"HTTP {resp.status_code}\n{text}"
