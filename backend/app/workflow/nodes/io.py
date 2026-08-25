"""
I/O & infrastructure nodes: http.request, db.query, file.read/write/parse,
connector.* (curated integrations), ssh.exec, browser, doc.convert.

Self-contained: depends only on stdlib, optional third-party packages (httpx,
asyncpg, paramiko, playwright, markitdown), ``settings`` and the shared helpers
in ``app.workflow.context`` — never the engine. The engine re-imports
``_exec_http_request`` / ``_exec_db_query`` for its stateless remote-runner path.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import aiosqlite

from app.core.config import settings
from app.services import rate_limiting
from app.workflow.context import (
    _as_bool,
    _DB_QUERY_MAX_ROWS,
    _FILE_MAX_BYTES,
    _HTTP_MAX_TIMEOUT,
    _safe_workspace_path,
    _TOOL_RESULT_MAX_CHARS,
)
from app.workflow.registry import DispatchCtx, node


# ── http.request + per-host rate limiting (fase 6.6) ─────────────────────────

# The per-host window is owned by services.rate_limiting now (roadmap v2 § 3,
# P2 — it can be shared across instances via RATE_LIMIT_BACKEND=database).
# _rate_hits stays bound to the in-memory limiter's own store so anything that
# inspected or primed it keeps working.
_rate_hits: dict[str, list[float]] = rate_limiting._memory_limiter.hits
_global_rate_limits: dict[str, int] | None = None  # parsed lazily from settings


def _parse_rate_limits(raw: str) -> dict[str, int]:
    """GRAPH_WORKFLOW_RATE_LIMITS: a JSON object {host: rpm} or 'host=rpm'
    pairs separated by commas. Invalid entries are dropped."""
    text = (raw or "").strip()
    out: dict[str, int] = {}
    if not text:
        return out
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return out
        if isinstance(data, dict):
            for host, rpm in data.items():
                try:
                    out[str(host).lower()] = max(1, int(rpm))
                except (TypeError, ValueError):
                    continue
        return out
    for pair in text.split(","):
        host, _, rpm = pair.partition("=")
        try:
            out[host.strip().lower()] = max(1, int(rpm))
        except (TypeError, ValueError):
            continue
    return {h: r for h, r in out.items() if h}


def _host_rate_limit(host: str, node_rpm) -> int | None:
    """The effective requests-per-minute cap for a host: the stricter of the
    node's own maxRequestsPerMinute and the global per-domain map (None = no cap)."""
    global _global_rate_limits
    if _global_rate_limits is None:
        _global_rate_limits = _parse_rate_limits(settings.graph_workflow_rate_limits)
    caps: list[int] = []
    global_cap = _global_rate_limits.get(host.lower())
    if global_cap:
        caps.append(global_cap)
    try:
        rpm = int(node_rpm or 0)
        if rpm > 0:
            caps.append(rpm)
    except (TypeError, ValueError):
        pass
    return min(caps) if caps else None


async def _rate_limit_admit(host: str, rpm: int) -> float:
    """Block until the host's window has a free slot, and report the wait.

    Throttling rather than failing is deliberate: the wait shows up as
    ``rated_limited_s`` in the node output instead of turning into an error.
    """
    return await rate_limiting.get_limiter().admit(host, rpm, 60.0)


async def _exec_http_request(params: dict) -> dict:
    """Generic HTTP call. Non-2xx raises by default so retry/onError apply;
    set ``allow_errors`` to get the response back regardless of status.
    Fase 6.6 — calls are throttled per host (node maxRequestsPerMinute and/or
    the global GRAPH_WORKFLOW_RATE_LIMITS map); throttled requests wait."""
    from urllib.parse import urlparse

    import httpx

    url = str(params.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("http.request: 'url' must be an http(s) URL")
    method = str(params.get("method") or "GET").upper()

    host = (urlparse(url).hostname or "").lower()
    rate_limited_s = 0.0
    rpm = _host_rate_limit(host, params.get("maxRequestsPerMinute")) if host else None
    if rpm:
        rate_limited_s = await _rate_limit_admit(host, rpm)

    headers = params.get("headers") if isinstance(params.get("headers"), dict) else None
    query = params.get("query") if isinstance(params.get("query"), dict) else None
    timeout = min(float(params.get("timeout") or 30.0), _HTTP_MAX_TIMEOUT)

    body = params.get("body")
    body_kwargs: dict = {}
    if isinstance(body, (dict, list)):
        body_kwargs["json"] = body
    elif body is not None and str(body) != "":
        body_kwargs["content"] = str(body)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.request(method, url, params=query, headers=headers, **body_kwargs)

    text = resp.text
    if len(text) > _TOOL_RESULT_MAX_CHARS:
        text = text[:_TOOL_RESULT_MAX_CHARS] + "\n[Truncated]"
    parsed = None
    if "json" in (resp.headers.get("content-type") or ""):
        try:
            parsed = resp.json()
        except ValueError:
            parsed = None

    if not resp.is_success and not _as_bool(params.get("allow_errors")):
        raise RuntimeError(f"http.request: {method} {url} → HTTP {resp.status_code}: {text[:300]}")

    out = {
        "status": resp.status_code,
        "ok": resp.is_success,
        "headers": dict(resp.headers),
        "json": parsed,
        "text": text,
    }
    if rate_limited_s > 0:
        out["rate_limited_s"] = round(rate_limited_s, 2)
    return out


# ── db.query ─────────────────────────────────────────────────────────────────

async def _exec_db_query(params: dict) -> dict:
    """Parameterised SQL. sqlite databases live inside the workspace storage;
    postgres connects via a DSN (typically ``={{ $secrets.PG_DSN }}``). Output:
    ``{rows, count, rowcount}`` (rows capped at 1000)."""
    query = str(params.get("query") or "").strip()
    if not query:
        raise ValueError("db.query: 'query' is required")
    args = params.get("params")
    if not isinstance(args, list):
        args = [] if args in (None, "") else [args]
    driver = str(params.get("driver") or "sqlite").strip().lower()

    if driver == "sqlite":
        path = _safe_workspace_path(params.get("database"), create_dirs=True)
        conn = await aiosqlite.connect(path)
        try:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(query, args)
            rows = [dict(r) for r in await cur.fetchmany(_DB_QUERY_MAX_ROWS)] if cur.description else []
            rowcount = cur.rowcount
            await conn.commit()
        finally:
            await conn.close()
        return {"rows": rows, "count": len(rows), "rowcount": rowcount}

    if driver == "postgres":
        dsn = str(params.get("dsn") or "").strip()
        if not dsn:
            raise ValueError("db.query: postgres needs a 'dsn' (use ={{ $secrets.<name> }})")
        try:
            import asyncpg  # noqa: PLC0415 — optional dependency
        except ImportError:
            raise RuntimeError(
                "db.query: postgres support requires the 'asyncpg' package in the backend image"
            ) from None
        conn = await asyncpg.connect(dsn=dsn, timeout=15)
        try:
            records = await conn.fetch(query, *args)
            rows = [dict(r) for r in records[:_DB_QUERY_MAX_ROWS]]
        finally:
            await conn.close()
        return {"rows": rows, "count": len(rows), "rowcount": len(rows)}

    raise ValueError(f"db.query: unknown driver {driver!r} (sqlite|postgres)")


# ── file.read / file.write / file.parse ──────────────────────────────────────

def _file_format(params: dict, path) -> str:
    fmt = str(params.get("format") or "auto").strip().lower()
    if fmt != "auto":
        return fmt
    suffix = str(getattr(path, "suffix", "") or "").lower()
    return {".json": "json", ".csv": "csv"}.get(suffix, "text")


def _parse_structured(text: str, fmt: str, delimiter: str) -> dict:
    """Shared by file.read and file.parse: a text payload → structured output."""
    import csv
    import io

    if fmt == "json":
        try:
            return {"data": json.loads(text)}
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc}") from None
    if fmt == "csv":
        rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter or ","))
        return {"rows": rows, "count": len(rows)}
    if fmt == "lines":
        lines = [ln for ln in text.splitlines() if ln.strip() != ""]
        return {"lines": lines, "count": len(lines)}
    return {"text": text, "size": len(text.encode("utf-8"))}


async def _exec_file_read(params: dict) -> dict:
    path = _safe_workspace_path(params.get("path"))
    if not path.is_file():
        raise FileNotFoundError(f"file.read: {params.get('path')!r} not found in the workspace storage")
    if path.stat().st_size > _FILE_MAX_BYTES:
        raise ValueError(f"file.read: file exceeds the {_FILE_MAX_BYTES // (1024 * 1024)} MB limit")
    encoding = str(params.get("encoding") or "utf-8")
    text = await asyncio.to_thread(path.read_text, encoding)
    fmt = _file_format(params, path)
    return {"path": str(params.get("path")), "format": fmt,
            **_parse_structured(text, fmt, str(params.get("delimiter") or ","))}


def _render_file_content(content, fmt: str, delimiter: str) -> str:
    import csv
    import io

    if fmt == "json" or (fmt == "text" and isinstance(content, (dict, list))):
        return json.dumps(content, indent=2, ensure_ascii=False, default=str)
    if fmt == "csv":
        rows = content if isinstance(content, list) else [content]
        if not rows:
            return ""
        buf = io.StringIO()
        if all(isinstance(r, dict) for r in rows):
            fieldnames: list[str] = []
            for r in rows:
                fieldnames.extend(k for k in r if k not in fieldnames)
            writer = csv.DictWriter(buf, fieldnames=fieldnames, delimiter=delimiter or ",")
            writer.writeheader()
            writer.writerows(rows)
        else:
            plain = csv.writer(buf, delimiter=delimiter or ",")
            for r in rows:
                plain.writerow(r if isinstance(r, (list, tuple)) else [r])
        return buf.getvalue()
    return content if isinstance(content, str) else json.dumps(content, default=str, ensure_ascii=False)


async def _exec_file_write(params: dict, node_input) -> dict:
    path = _safe_workspace_path(params.get("path"), create_dirs=True)
    content = params.get("content")
    if content is None:
        content = node_input
    fmt = _file_format(params, path)
    text = _render_file_content(content, fmt, str(params.get("delimiter") or ","))
    if len(text.encode("utf-8")) > _FILE_MAX_BYTES:
        raise ValueError(f"file.write: content exceeds the {_FILE_MAX_BYTES // (1024 * 1024)} MB limit")
    append = _as_bool(params.get("append"))

    def _write() -> int:
        mode = "a" if append else "w"
        with open(path, mode, encoding=str(params.get("encoding") or "utf-8")) as fh:
            return fh.write(text)

    written = await asyncio.to_thread(_write)
    return {"path": str(params.get("path")), "format": fmt,
            "bytes_written": len(text.encode("utf-8")), "chars_written": written, "append": append}


def _exec_file_parse(params: dict, node_input) -> dict:
    """Parse an in-flight text payload (an http.request body, a tool result…)
    without touching disk. ``content`` defaults to the node input."""
    content = params.get("content")
    if content is None or content == "":
        content = node_input
    fmt = str(params.get("format") or "auto").strip().lower()
    if not isinstance(content, str):
        # Already-structured input passes through as parsed data.
        return {"data": content} if fmt in ("auto", "json") else {"rows": content if isinstance(content, list) else [content], "count": len(content) if isinstance(content, list) else 1}
    if fmt == "auto":
        stripped = content.strip()
        fmt = "json" if stripped[:1] in ("{", "[") else "csv" if ("," in stripped.splitlines()[0] if stripped else False) else "lines"
    return _parse_structured(content, fmt, str(params.get("delimiter") or ","))


# ── Phase 47 (roadmap fase 15) — connectors and multimodal nodes ────────────

def _connector_slack_post(p: dict) -> dict:
    return {
        "method": "POST", "url": "https://slack.com/api/chat.postMessage",
        "headers": {"Authorization": f"Bearer {p.get('token', '')}"},
        "body": {"channel": p.get("channel"), "text": p.get("text"),
                 **({"thread_ts": p["thread_ts"]} if p.get("thread_ts") else {})},
    }


def _connector_discord_post(p: dict) -> dict:
    return {"method": "POST", "url": str(p.get("webhook_url") or ""),
            "body": {"content": p.get("text"),
                     **({"username": p["username"]} if p.get("username") else {})}}


def _connector_github_issue(p: dict) -> dict:
    return {
        "method": "POST",
        "url": f"https://api.github.com/repos/{p.get('repo', '')}/issues",
        "headers": {"Authorization": f"Bearer {p.get('token', '')}",
                    "Accept": "application/vnd.github+json"},
        "body": {"title": p.get("title"), "body": p.get("body"),
                 **({"labels": p["labels"]} if p.get("labels") else {})},
    }


def _connector_gitlab_issue(p: dict) -> dict:
    from urllib.parse import quote

    base = str(p.get("base_url") or "https://gitlab.com").rstrip("/")
    project = quote(str(p.get("project") or ""), safe="")
    return {
        "method": "POST", "url": f"{base}/api/v4/projects/{project}/issues",
        "headers": {"PRIVATE-TOKEN": str(p.get("token") or "")},
        "body": {"title": p.get("title"), "description": p.get("body"),
                 **({"labels": p["labels"]} if p.get("labels") else {})},
    }


def _connector_jira_issue(p: dict) -> dict:
    import base64

    base = str(p.get("base_url") or "").rstrip("/")
    token = base64.b64encode(f"{p.get('email', '')}:{p.get('token', '')}".encode()).decode()
    return {
        "method": "POST", "url": f"{base}/rest/api/3/issue",
        "headers": {"Authorization": f"Basic {token}"},
        "body": {"fields": {
            "project": {"key": p.get("project_key")},
            "summary": p.get("summary"),
            "issuetype": {"name": p.get("issue_type") or "Task"},
            **({"description": p["description"]} if p.get("description") else {}),
        }},
    }


def _connector_sheets_append(p: dict) -> dict:
    rng = str(p.get("range") or "Sheet1!A1")
    return {
        "method": "POST",
        "url": (f"https://sheets.googleapis.com/v4/spreadsheets/"
                f"{p.get('spreadsheet_id', '')}/values/{rng}:append"),
        "query": {"valueInputOption": p.get("value_input_option") or "USER_ENTERED"},
        "headers": {"Authorization": f"Bearer {p.get('token', '')}"},
        "body": {"values": p.get("values") or []},
    }


def _connector_sheets_read(p: dict) -> dict:
    rng = str(p.get("range") or "Sheet1!A1:Z1000")
    return {
        "method": "GET",
        "url": (f"https://sheets.googleapis.com/v4/spreadsheets/"
                f"{p.get('spreadsheet_id', '')}/values/{rng}"),
        "headers": {"Authorization": f"Bearer {p.get('token', '')}"},
    }


# Registry of curated integrations (15.1). Each entry maps the connector's
# operation params to an http.request spec; auth values arrive already resolved
# from $secrets via the expression layer. Adding a service is a one-line entry
# — the dispatch, retry, node-test and pin machinery come for free.
_CONNECTORS: dict[str, callable] = {
    "slack.postMessage": _connector_slack_post,
    "discord.postMessage": _connector_discord_post,
    "github.createIssue": _connector_github_issue,
    "gitlab.createIssue": _connector_gitlab_issue,
    "jira.createIssue": _connector_jira_issue,
    "sheets.append": _connector_sheets_append,
    "sheets.read": _connector_sheets_read,
}


def _connector_request(operation: str, params: dict) -> dict:
    """Pure mapper (unit-testable, no I/O): connector operation + params → the
    http.request params the engine would issue. Raises for an unknown op."""
    builder = _CONNECTORS.get(operation)
    if builder is None:
        raise ValueError(
            f"connector: unknown operation '{operation}' "
            f"(known: {', '.join(sorted(_CONNECTORS))})"
        )
    spec = builder(params)
    # Carry through the shared http.request knobs so retry/rate-limit still apply.
    for passthrough in ("timeout", "allow_errors", "maxRequestsPerMinute"):
        if params.get(passthrough) is not None:
            spec[passthrough] = params[passthrough]
    return spec


async def _exec_connector(operation: str, params: dict, node_input) -> dict:
    """15.1 — execute a curated connector as an http.request. Output is the
    http.request output plus the ``operation`` that produced it."""
    spec = _connector_request(operation, params)
    out = await _exec_http_request(spec)
    out["operation"] = operation
    return out


# ── ssh.exec ─────────────────────────────────────────────────────────────────

def _ssh_host_allowed(host: str) -> bool:
    allowed = [h.strip().lower() for h in settings.graph_workflow_ssh_allowed_hosts.split(",") if h.strip()]
    return not allowed or host.lower() in allowed


async def _exec_ssh_exec(params: dict) -> dict:
    """15.2 — run a command on a remote host over SSH. Credentials (key or
    password) come from $secrets; the host must pass the per-instance allow-list.
    Output: {stdout, stderr, exit_code}. A non-zero exit raises unless
    ``allow_nonzero`` is set (so retry / On error apply)."""
    host = str(params.get("host") or "").strip()
    if not host:
        raise ValueError("ssh.exec: 'host' is required")
    if not _ssh_host_allowed(host):
        raise ValueError(f"ssh.exec: host '{host}' is not in GRAPH_WORKFLOW_SSH_ALLOWED_HOSTS")
    command = str(params.get("command") or "").strip()
    if not command:
        raise ValueError("ssh.exec: 'command' is required")

    try:
        import paramiko  # noqa: PLC0415 — optional dependency
    except ImportError:
        raise RuntimeError("ssh.exec: the 'paramiko' package is required in the backend image") from None

    port = int(params.get("port") or 22)
    username = str(params.get("username") or "").strip()
    password = params.get("password")
    private_key = params.get("private_key")
    timeout = min(float(params.get("timeout") or settings.graph_workflow_ssh_timeout_seconds),
                  float(settings.graph_workflow_ssh_timeout_seconds))

    def _run() -> dict:
        import io

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs: dict = {"hostname": host, "port": port, "username": username, "timeout": timeout}
        if private_key:
            connect_kwargs["pkey"] = paramiko.RSAKey.from_private_key(io.StringIO(str(private_key)))
        elif password is not None:
            connect_kwargs["password"] = str(password)
        client.connect(**connect_kwargs)
        try:
            _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")[:_TOOL_RESULT_MAX_CHARS]
            err = stderr.read().decode("utf-8", errors="replace")[:_TOOL_RESULT_MAX_CHARS]
            code = stdout.channel.recv_exit_status()
            return {"stdout": out, "stderr": err, "exit_code": code}
        finally:
            client.close()

    result = await asyncio.to_thread(_run)
    if result["exit_code"] != 0 and not _as_bool(params.get("allow_nonzero")):
        raise RuntimeError(
            f"ssh.exec: '{command}' exited {result['exit_code']}: {result['stderr'][:300]}"
        )
    return result


# ── browser (Playwright) ─────────────────────────────────────────────────────

async def _exec_browser(params: dict) -> dict:
    """15.3 — drive a headless browser (Playwright): open a URL, optionally wait
    for a selector, then extract text / an attribute / a rendered screenshot
    (saved to the workspace storage). Output depends on ``action``. Runs in a
    thread with a per-action timeout; a missing Playwright raises clearly."""
    url = str(params.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("browser: 'url' must be an http(s) URL")
    action = str(params.get("action") or "text").strip().lower()
    selector = str(params.get("selector") or "").strip()
    timeout_ms = int(min(float(params.get("timeout") or settings.graph_workflow_browser_timeout_seconds),
                         float(settings.graph_workflow_browser_timeout_seconds)) * 1000)

    screenshot_path = None
    if action == "screenshot":
        screenshot_path = _safe_workspace_path(
            params.get("screenshot_path") or f"browser/{uuid.uuid4().hex}.png", create_dirs=True,
        )

    def _run() -> dict:
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415 — optional dependency
        except ImportError:
            raise RuntimeError(
                "browser: the 'playwright' package (and a browser: playwright install chromium) "
                "is required in the backend image"
            ) from None

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                if selector:
                    page.wait_for_selector(selector, timeout=timeout_ms)
                target = page.locator(selector) if selector else None
                if action == "screenshot":
                    from pathlib import Path  # noqa: PLC0415

                    (target or page).screenshot(path=str(screenshot_path))
                    root = Path(settings.graph_workflow_files_dir).resolve()
                    return {"action": action, "url": url,
                            "path": str(screenshot_path.relative_to(root))}
                if action == "attribute":
                    attr = str(params.get("attribute") or "href")
                    return {"action": action, "url": url, "attribute": attr,
                            "value": (target or page).first.get_attribute(attr) if target else None}
                # default: extract text (of the selector, or the whole page)
                text = (target.first.inner_text() if target else page.inner_text("body"))
                return {"action": "text", "url": url,
                        "text": text[:_TOOL_RESULT_MAX_CHARS], "title": page.title()}
            finally:
                browser.close()

    return await asyncio.to_thread(_run)


# ── doc.convert ──────────────────────────────────────────────────────────────

async def _exec_doc_convert(params: dict, node_input) -> dict:
    """15.5 — convert a PDF/DOCX/HTML/… document from the workspace storage to
    markdown via markitdown (already in the backend image for the KB). Output:
    {markdown, chars, path}. ``path`` defaults to the node input."""
    raw = params.get("path")
    if raw in (None, "") and isinstance(node_input, str):
        raw = node_input
    path = _safe_workspace_path(raw)
    if not path.is_file():
        raise FileNotFoundError(f"doc.convert: {raw!r} not found in the workspace storage")
    if path.stat().st_size > _FILE_MAX_BYTES:
        raise ValueError(f"doc.convert: file exceeds the {_FILE_MAX_BYTES // (1024 * 1024)} MB limit")

    def _convert() -> str:
        from markitdown import MarkItDown  # noqa: PLC0415 — optional dependency

        return MarkItDown().convert(str(path)).text_content or ""

    try:
        markdown = await asyncio.to_thread(_convert)
    except ImportError:
        raise RuntimeError("doc.convert: the 'markitdown' package is required in the backend image") from None
    markdown = markdown[:_TOOL_RESULT_MAX_CHARS * 4]
    return {"path": str(raw), "markdown": markdown, "chars": len(markdown)}


# ── handlers ─────────────────────────────────────────────────────────────────

@node("http.request")
async def _h_http_request(c: DispatchCtx):
    return await _exec_http_request(c.params), ["main"]


@node("db.query")
async def _h_db_query(c: DispatchCtx):
    return await _exec_db_query(c.params), ["main"]


@node("file.read")
async def _h_file_read(c: DispatchCtx):
    return await _exec_file_read(c.params), ["main"]


@node("file.write")
async def _h_file_write(c: DispatchCtx):
    return await _exec_file_write(c.params, c.node_input), ["main"]


@node("file.parse")
async def _h_file_parse(c: DispatchCtx):
    return _exec_file_parse(c.params, c.node_input), ["main"]


@node("connector.", prefix=True)
async def _h_connector(c: DispatchCtx):
    # 15.1 — curated integration over http.request; auth from $secrets is
    # already resolved into params by the expression layer.
    return await _exec_connector(c.ntype[len("connector."):], c.params, c.node_input), ["main"]


@node("ssh.exec")
async def _h_ssh_exec(c: DispatchCtx):
    return await _exec_ssh_exec(c.params), ["main"]


@node("browser")
async def _h_browser(c: DispatchCtx):
    return await _exec_browser(c.params), ["main"]


@node("doc.convert")
async def _h_doc_convert(c: DispatchCtx):
    return await _exec_doc_convert(c.params, c.node_input), ["main"]
