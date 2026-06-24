import ast
import logging
import math
import operator as op
import re
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

_ALLOWED_OPS = {
    ast.Add: op.add, ast.Sub: op.sub,
    ast.Mult: op.mul, ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv, ast.Mod: op.mod,
    ast.Pow: op.pow, ast.USub: op.neg, ast.UAdd: op.pos,
}

_ALLOWED_FUNCS: dict = {
    'sqrt': math.sqrt, 'abs': abs, 'round': round,
    'floor': math.floor, 'ceil': math.ceil,
    'log': math.log, 'log10': math.log10,
    'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
    'pi': math.pi, 'e': math.e,
}


def _safe_eval(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Only numeric constants allowed")
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_OPS:
            raise ValueError(f"Operator not allowed: {op_type.__name__}")
        return _ALLOWED_OPS[op_type](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_OPS:
            raise ValueError(f"Operator not allowed: {op_type.__name__}")
        return _ALLOWED_OPS[op_type](_safe_eval(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise ValueError(f"Function not allowed: {getattr(node.func, 'id', '?')}")
        args = [_safe_eval(a) for a in node.args]
        return _ALLOWED_FUNCS[node.func.id](*args)
    if isinstance(node, ast.Name):
        if node.id in _ALLOWED_FUNCS:
            return _ALLOWED_FUNCS[node.id]
        raise ValueError(f"Name not allowed: {node.id}")
    raise ValueError(f"Expression type not allowed: {type(node).__name__}")


async def get_datetime(timezone: str = "UTC") -> str:
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(timezone)
        now = datetime.now(tz)
        return now.strftime(f"%Y-%m-%d %H:%M:%S %Z ({timezone})")
    except Exception:  # noqa: BLE001
        now = datetime.utcnow()
        return now.strftime("%Y-%m-%d %H:%M:%S UTC (timezone not recognised, returned UTC)")


async def calculator(expression: str) -> str:
    try:
        tree = ast.parse(expression.strip(), mode='eval')
        result = _safe_eval(tree.body)
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return str(result)
    except ZeroDivisionError:
        return "Error: division by zero"
    except (ValueError, TypeError) as exc:
        return f"Error: {exc}"


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode common entities."""
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
               .replace('&quot;', '"').replace('&#x27;', "'").replace('&nbsp;', ' ')
    return re.sub(r'\s+', ' ', text).strip()


async def read_url(url: str, max_chars: int = 4000) -> str:
    """Fetch a web page and return its plain-text content, stripped of HTML."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "text" not in content_type and "json" not in content_type:
                return f"Cannot read binary content at {url} (content-type: {content_type})"
            text = resp.text

        # Remove script / style blocks first
        text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', text, flags=re.DOTALL | re.IGNORECASE)
        # Strip remaining tags
        text = re.sub(r'<[^>]+>', ' ', text)
        # Decode common HTML entities
        text = (text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
                    .replace('&quot;', '"').replace('&#x27;', "'").replace('&nbsp;', ' '))
        # Collapse whitespace / blank lines
        text = re.sub(r'\n{3,}', '\n\n', re.sub(r'[ \t]+', ' ', text)).strip()

        if len(text) > max_chars:
            text = text[:max_chars] + f'\n\n[Truncated — {len(text) - max_chars} chars omitted]'
        logger.debug("read_url fetched %d chars from %s", len(text), url)
        return text or f"No readable text found at {url}"

    except (httpx.HTTPError, OSError) as exc:
        logger.warning("read_url failed for %s: %s", url, exc)
        return f"Error fetching {url}: {exc}"


async def web_search(query: str, max_results: int = 3) -> str:
    """
    Search the web using DuckDuckGo HTML search and return plain-text snippets.

    Falls back to the DDG instant-answer JSON API if HTML scraping yields nothing.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Primary: DDG HTML search — richer results than the instant-answer API
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query, "kl": "us-en"},
                headers=headers,
            )
            resp.raise_for_status()
            html = resp.text

        # Extract result blocks: <a class="result__a"> for title, <a class="result__snippet"> for body
        snippets = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        )
        titles = re.findall(
            r'class="result__a"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        )

        parts: list[str] = []
        for title, snippet in zip(titles[:max_results], snippets[:max_results]):
            clean_title = _strip_html(title)
            clean_snippet = _strip_html(snippet)
            if clean_snippet:
                parts.append(f"**{clean_title}**\n{clean_snippet}")

        if parts:
            return "\n\n".join(parts)

        logger.debug("DDG HTML search returned no snippets for query=%r, trying instant API", query)

    except (httpx.HTTPError, OSError) as exc:
        logger.warning("DDG HTML search failed for query=%r: %s", query, exc)

    # Fallback: DDG instant-answer JSON API (good for well-known topics / definitions)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                headers={"User-Agent": "SpiceSibyl/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()

        parts = []
        if data.get("AbstractText"):
            parts.append(data["AbstractText"])
        for topic in (data.get("RelatedTopics") or [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                parts.append(f"• {topic['Text']}")
        if parts:
            return "\n".join(parts)

    except (httpx.HTTPError, OSError) as exc:
        logger.warning("DDG instant API fallback failed for query=%r: %s", query, exc)

    return f"No results found for: {query}"
