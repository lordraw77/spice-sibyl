"""
Phase 18 — minimal MCP client (stdio + SSE transports).

The official ``mcp`` Python SDK requires Python 3.10+, but this service targets
3.9, so we implement just the slice of the Model Context Protocol we need:
the ``initialize`` handshake, ``tools/list`` and ``tools/call`` over JSON-RPC 2.0.

Two transports are supported:

* **stdio** — spawn ``command``/``args`` and exchange newline-delimited JSON-RPC
  over stdin/stdout. Sessions are short-lived (spawn → query → shut down), which
  matches the common ``docker run --rm -i … run_stdio.py`` deployment.
* **sse** — open an HTTP+SSE stream to ``url``; the server replies with an
  ``endpoint`` event giving the URL to POST JSON-RPC requests to, and responses
  arrive back as SSE ``message`` events.

Both transports expose the same protocol surface via :class:`_McpProtocol`.
"""

import asyncio
import json
import logging
import os
import shutil
from contextlib import asynccontextmanager
from urllib.parse import urljoin

import httpx

from app.schemas.mcp import McpServerConfig, McpToolInfo

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "spice-sibyl", "version": "1.0"}

# A single MCP server launch (spawn + handshake) shouldn't take long; tool calls
# may run longer (e.g. a Docker container doing work).
DEFAULT_CONNECT_TIMEOUT = 30.0
DEFAULT_CALL_TIMEOUT = 120.0


class MCPError(RuntimeError):
    """Raised when an MCP server errors, times out, or speaks malformed JSON-RPC."""


# Phase 23.5.b — launchers a stdio MCP server's `command` might name, reported
# by GET /v1/mcp/runtimes so operators can see what the running image supports.
KNOWN_RUNTIMES = ("docker", "node", "npx", "uv", "uvx", "python")


def detect_runtimes() -> dict[str, bool]:
    """Which known stdio launchers are present on PATH in this image."""
    return {name: shutil.which(name) is not None for name in KNOWN_RUNTIMES}


def _allowed_commands() -> set[str]:
    from app.core.config import settings

    return {c.strip() for c in settings.mcp_allowed_commands.split(",") if c.strip()}


def _command_allowed(command_name: str, allowed: set[str]) -> bool:
    """True if ``command_name`` matches an allowlist entry.

    Matches versioned interpreter names too (``python3.12`` / ``node20`` match
    the ``python`` / ``node`` allowlist entries) since ``sys.executable`` and
    distro packages rarely ship the bare name.
    """
    if not allowed:
        return True
    if command_name in allowed:
        return True
    for entry in allowed:
        if command_name.startswith(entry) and (
            len(command_name) == len(entry) or not command_name[len(entry)].isalpha()
        ):
            return True
    return False


# ── Shared JSON-RPC protocol surface ──────────────────────────────────────────
class _McpProtocol:
    """MCP request/response logic, independent of transport.

    Subclasses implement :meth:`request` and :meth:`notify`; everything the
    manager needs (handshake, tool discovery, tool calls) is built on top.
    """

    async def request(self, method: str, params: dict | None = None, timeout: float = DEFAULT_CALL_TIMEOUT) -> dict:
        raise NotImplementedError

    async def notify(self, method: str, params: dict | None = None) -> None:
        raise NotImplementedError

    async def initialize(self, timeout: float = DEFAULT_CONNECT_TIMEOUT) -> dict:
        result = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": _CLIENT_INFO,
            },
            timeout=timeout,
        )
        await self.notify("notifications/initialized")
        return result

    async def list_tools(self, timeout: float = DEFAULT_CONNECT_TIMEOUT) -> list[McpToolInfo]:
        result = await self.request("tools/list", {}, timeout=timeout)
        tools = []
        for t in result.get("tools", []):
            tools.append(
                McpToolInfo(
                    name=t.get("name", ""),
                    description=t.get("description", "") or "",
                    input_schema=t.get("inputSchema") or t.get("input_schema") or {},
                )
            )
        return tools

    async def call_tool(self, name: str, arguments: dict, timeout: float = DEFAULT_CALL_TIMEOUT) -> str:
        result = await self.request(
            "tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout
        )
        from app.core.config import settings

        if settings.mcp_log_calls:
            logger.info(
                "MCP tools/call '%s' raw result: %s",
                name,
                _truncate(json.dumps(result, ensure_ascii=False), settings.mcp_log_max_chars),
            )
        return _flatten_content(result)


# ── stdio transport ───────────────────────────────────────────────────────────
class _StdioSession(_McpProtocol):
    """A JSON-RPC session over a spawned process's stdin/stdout."""

    def __init__(self, proc: asyncio.subprocess.Process):
        self._proc = proc
        self._next_id = 0

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _exit_suffix(self) -> str:
        rc = self._proc.returncode
        return f" (process exited with code {rc})" if rc is not None else ""

    async def _send(self, message: dict) -> None:
        if self._proc.stdin is None:
            raise MCPError("MCP server has no stdin pipe")
        line = json.dumps(message) + "\n"
        try:
            self._proc.stdin.write(line.encode("utf-8"))
            await self._proc.stdin.drain()
        except (OSError, RuntimeError) as exc:
            # The child died before reading us (bad image, missing runtime, exec
            # failure): uvloop raises RuntimeError on the closed write transport,
            # asyncio raises BrokenPipeError. Both mean the same thing here — and
            # both must surface as MCPError so the caller can report stderr.
            suffix = self._exit_suffix() or f": {exc}"
            raise MCPError(f"MCP server process is not accepting input{suffix}") from exc

    async def _read_result(self, expected_id: int, timeout: float) -> dict:
        """Read lines until the response with ``expected_id`` arrives.

        Notifications and unrelated responses (the server may interleave log
        notifications) are skipped. Raises MCPError on timeout, EOF, or an
        error response.
        """
        if self._proc.stdout is None:
            raise MCPError("MCP server has no stdout pipe")
        while True:
            try:
                raw = await asyncio.wait_for(self._proc.stdout.readline(), timeout)
            except asyncio.TimeoutError as exc:
                raise MCPError(f"timed out waiting for response id={expected_id}") from exc
            except (OSError, RuntimeError) as exc:
                # Same failure mode as _send: the read transport is gone because
                # the child process died.
                raise MCPError(f"MCP server closed the connection{self._exit_suffix()}") from exc
            if not raw:
                raise MCPError(f"MCP server closed the connection unexpectedly{self._exit_suffix()}")
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                # Some servers print non-protocol noise to stdout; ignore it.
                logger.debug("ignoring non-JSON line from MCP server: %s", line[:200])
                continue
            if msg.get("id") != expected_id:
                continue
            if "error" in msg:
                err = msg["error"] or {}
                raise MCPError(f"MCP error {err.get('code')}: {err.get('message')}")
            return msg.get("result") or {}

    async def request(self, method: str, params: dict | None = None, timeout: float = DEFAULT_CALL_TIMEOUT) -> dict:
        rid = self._new_id()
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        return await self._read_result(rid, timeout)

    async def notify(self, method: str, params: dict | None = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})


# ── SSE transport ─────────────────────────────────────────────────────────────
class _SseSession(_McpProtocol):
    """A JSON-RPC session over the MCP HTTP+SSE transport.

    Responses arrive asynchronously on the SSE stream, so requests register a
    future keyed by id; a background reader resolves them as messages come in.
    """

    def __init__(self, client: httpx.AsyncClient, stream_response: httpx.Response, base_url: str):
        self._client = client
        self._stream = stream_response
        self._base_url = base_url
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._endpoint: str | None = None
        self._endpoint_ready = asyncio.Event()
        self._reader_task: asyncio.Task | None = None

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def start(self, connect_timeout: float) -> None:
        self._reader_task = asyncio.create_task(self._read_loop())
        try:
            await asyncio.wait_for(self._endpoint_ready.wait(), connect_timeout)
        except asyncio.TimeoutError as exc:
            raise MCPError("SSE server did not advertise an 'endpoint' event in time") from exc

    async def _read_loop(self) -> None:
        event: str | None = None
        data_lines: list[str] = []
        try:
            async for line in self._stream.aiter_lines():
                if line == "":  # blank line terminates an SSE event
                    if data_lines:
                        self._handle_event(event or "message", "\n".join(data_lines))
                    event, data_lines = None, []
                    continue
                if line.startswith(":"):  # comment / keep-alive
                    continue
                field, _, value = line.partition(":")
                value = value[1:] if value.startswith(" ") else value
                if field == "event":
                    event = value.strip()
                elif field == "data":
                    data_lines.append(value)
        except Exception as exc:  # noqa: BLE001 — surface any stream failure to callers
            self._fail_all(MCPError(f"SSE stream error: {exc}"))
            return
        self._fail_all(MCPError("SSE stream closed by server"))

    def _handle_event(self, event: str, data: str) -> None:
        if event == "endpoint":
            # The endpoint may be an absolute URL or a path relative to base_url.
            self._endpoint = urljoin(self._base_url, data.strip())
            self._endpoint_ready.set()
            return
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            logger.debug("ignoring non-JSON SSE data: %s", data[:200])
            return
        mid = msg.get("id")
        if mid is None:
            return  # server notification — nothing awaiting it
        fut = self._pending.pop(mid, None)
        if fut is None or fut.done():
            return
        if "error" in msg:
            err = msg["error"] or {}
            fut.set_exception(MCPError(f"MCP error {err.get('code')}: {err.get('message')}"))
        else:
            fut.set_result(msg.get("result") or {})

    def _fail_all(self, exc: MCPError) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    async def _post(self, message: dict) -> None:
        if self._endpoint is None:
            raise MCPError("SSE endpoint not ready")
        try:
            resp = await self._client.post(
                self._endpoint, json=message,
                headers={"Content-Type": "application/json"}, timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise MCPError(f"failed to POST to MCP endpoint: {exc}") from exc

    async def request(self, method: str, params: dict | None = None, timeout: float = DEFAULT_CALL_TIMEOUT) -> dict:
        rid = self._new_id()
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        await self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(rid, None)
            raise MCPError(f"timed out waiting for response to '{method}'") from exc

    async def notify(self, method: str, params: dict | None = None) -> None:
        await self._post({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 — best-effort cleanup
                pass


# ── helpers ───────────────────────────────────────────────────────────────────
def _truncate(text: str, max_chars: int) -> str:
    if max_chars and len(text) > max_chars:
        return f"{text[:max_chars]}… ({len(text)} chars total)"
    return text


def _flatten_content(result: dict) -> str:
    """Reduce a tools/call result's ``content`` blocks to a single text string."""
    parts: list[str] = []
    for block in result.get("content", []) or []:
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "resource":
            res = block.get("resource") or {}
            parts.append(res.get("text") or res.get("uri") or json.dumps(res))
        else:
            parts.append(json.dumps(block))
    text = "\n".join(p for p in parts if p)
    if result.get("isError"):
        return f"[tool error] {text}" if text else "[tool error]"
    return text or "(empty result)"


# ── session factory (dispatches on transport) ─────────────────────────────────
@asynccontextmanager
async def open_session(config: McpServerConfig, connect_timeout: float = DEFAULT_CONNECT_TIMEOUT):
    """Open an initialized MCP session for ``config`` (stdio or sse transport).

    Always tears the connection down on exit. Raises MCPError on launch/connect
    or handshake failure.
    """
    if config.transport == "sse":
        async with _open_sse(config, connect_timeout) as session:
            yield session
    else:
        async with _open_stdio(config, connect_timeout) as session:
            yield session


@asynccontextmanager
async def _open_stdio(config: McpServerConfig, connect_timeout: float):
    from app.core.config import settings

    # Phase 23.5.c — guardrails before spawning anything.
    if not settings.mcp_stdio_enabled:
        raise MCPError("stdio MCP servers are disabled (MCP_STDIO_ENABLED=false)")
    command_name = os.path.basename(config.command or "")
    allowed = _allowed_commands()
    if not _command_allowed(command_name, allowed):
        raise MCPError(
            f"command '{command_name}' is not in MCP_ALLOWED_COMMANDS "
            f"({', '.join(sorted(allowed))})"
        )
    # Phase 23.5.b — PATH preflight so a missing runtime gives an actionable
    # error instead of a raw OSError from create_subprocess_exec.
    if shutil.which(config.command or "") is None:
        raise MCPError(
            f"'{config.command}' not found in the backend image — enable the "
            "corresponding runtime layer (Phase 23.5.a), or use the docker/mcp-proxy transport"
        )

    env = dict(os.environ)
    env.update(config.env or {})
    try:
        proc = await asyncio.create_subprocess_exec(
            config.command,
            *config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=config.cwd or None,
        )
    except (OSError, ValueError) as exc:
        raise MCPError(f"failed to launch MCP server '{config.command}': {exc}") from exc

    session = _StdioSession(proc)
    try:
        try:
            await session.initialize(timeout=connect_timeout)
        except Exception as exc:  # noqa: BLE001 — includes transport-level errors
            # A child that dies before the handshake (missing image, denied
            # docker socket, bad entrypoint) can fail as MCPError *or* as a raw
            # transport error from the event loop. Either way the useful
            # information is on the process's stderr, so catch everything and
            # turn it into one actionable MCPError.
            detail = (await _drain_stderr(proc)).strip()
            rc = proc.returncode
            parts = [f"handshake failed for '{config.command}'"]
            if rc is not None:
                parts.append(f" (exit code {rc})")
            parts.append(f": {detail}" if detail else f": {exc}")
            raise MCPError("".join(parts)) from exc
        yield session
    finally:
        await _shutdown(proc)


@asynccontextmanager
async def _open_sse(config: McpServerConfig, connect_timeout: float):
    headers = {"Accept": "text/event-stream", **(config.headers or {})}
    # The SSE GET stays open for the session, so disable the read timeout on it.
    timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    stream_cm = client.stream("GET", config.url, headers=headers)
    try:
        response = await stream_cm.__aenter__()
    except httpx.HTTPError as exc:
        await client.aclose()
        raise MCPError(f"failed to connect to SSE MCP server '{config.url}': {exc}") from exc
    if response.status_code >= 400:
        await stream_cm.__aexit__(None, None, None)
        await client.aclose()
        raise MCPError(f"SSE MCP server '{config.url}' returned HTTP {response.status_code}")

    session = _SseSession(client, response, config.url)
    try:
        await session.start(connect_timeout)
        await session.initialize(timeout=connect_timeout)
        yield session
    finally:
        await session.close()
        try:
            await stream_cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
        await client.aclose()


async def _drain_stderr(proc: asyncio.subprocess.Process, limit: int = 4000) -> str:
    """Best-effort read of a failed MCP server's stderr.

    Never raises: a dead process may already have its read transport torn down.
    """
    if proc.stderr is None:
        return ""
    try:
        data = await asyncio.wait_for(proc.stderr.read(limit), 2.0)
    except (asyncio.TimeoutError, OSError, RuntimeError, ValueError):
        return ""
    return data.decode("utf-8", errors="replace")


async def _shutdown(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if proc.stdin is not None and not proc.stdin.is_closing():
            proc.stdin.close()
    except (OSError, RuntimeError):
        pass
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), 5.0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
