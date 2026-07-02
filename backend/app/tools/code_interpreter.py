"""
Phase 18 — sandboxed Python code interpreter (built-in tool).

Runs model-supplied Python in an isolated subprocess:

* fresh interpreter with ``-I`` (isolated: no site-packages injection from cwd,
  ignores PYTHON* env) and a minimal environment;
* an ephemeral working directory — input ``files`` are materialised there
  before the run, files the code creates are reported (small text files
  returned inline) and the directory is deleted afterwards;
* hard resource limits set in the child before exec: CPU seconds, address
  space, file size, open files, no new processes;
* network access disabled by a preamble that stubs out ``socket`` before the
  user code runs (defence in depth on top of the process limits);
* wall-clock timeout enforced with ``asyncio.wait_for`` (process killed).

This is containment for accidents and resource abuse, not a hostile-code jail:
run the backend container itself with least privilege for untrusted tenants.
"""

import asyncio
import logging
import os
import resource
import shutil
import sys
import tempfile

from app.core.config import settings

logger = logging.getLogger(__name__)

# Prepended to the user code inside the sandbox script. Blocks network access
# at the Python level; runs before any user import can grab a real socket.
_SANDBOX_PREAMBLE = """\
import builtins as _b, socket as _s

def _no_net(*_a, **_k):
    raise OSError("network access is disabled in the code interpreter sandbox")

for _name in ("socket", "socketpair", "create_connection", "create_server",
              "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
    setattr(_s, _name, _no_net)
del _b, _s, _no_net, _name
"""

_INLINE_FILE_MAX = 4000       # max chars of a created file returned inline
_MAX_INPUT_FILES = 16
_MAX_REPORTED_FILES = 16


def _child_limits() -> None:
    """Applied in the child between fork and exec (preexec_fn)."""
    cpu = max(1, int(settings.code_interpreter_timeout))
    mem = settings.code_interpreter_memory_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (16, 16))
    except (ValueError, OSError):
        pass  # not adjustable in some container runtimes
    os.setsid()  # own process group so cleanup can kill any stragglers


def _safe_name(name: str) -> str | None:
    """Reject path traversal in file names; allow simple relative names."""
    name = (name or "").strip()
    if not name or name.startswith(("/", "~")) or ".." in name.split("/"):
        return None
    return name


async def python_exec(code: str, files: dict | None = None) -> str:
    """Execute Python code in the sandbox; returns stdout/stderr + created files."""
    if not settings.code_interpreter_enabled:
        return "The code interpreter is disabled on this server (CODE_INTERPRETER_ENABLED=false)."
    if not (code or "").strip():
        return "Error: 'code' is empty."

    workdir = tempfile.mkdtemp(prefix="sibyl-pyexec-")
    try:
        # Materialise input files inside the sandbox dir.
        for name, content in list((files or {}).items())[:_MAX_INPUT_FILES]:
            safe = _safe_name(str(name))
            if not safe:
                return f"Error: invalid input file name '{name}'."
            path = os.path.join(workdir, safe)
            os.makedirs(os.path.dirname(path) or workdir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(str(content))

        script = os.path.join(workdir, "__main__.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(_SANDBOX_PREAMBLE + "\n" + code)
        pre_existing = _snapshot(workdir)

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-B", script,
            cwd=workdir,
            env={"PATH": "/usr/bin:/bin", "HOME": workdir, "LANG": "C.UTF-8"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=_child_limits,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=settings.code_interpreter_timeout + 2
            )
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, 9)
            except (ProcessLookupError, OSError):
                proc.kill()
            await proc.wait()
            return (
                f"Error: execution timed out after {settings.code_interpreter_timeout:.0f}s "
                "(the process was killed)."
            )

        max_chars = settings.code_interpreter_max_output_chars
        stdout = _truncate(stdout_b.decode("utf-8", "replace"), max_chars)
        stderr = _truncate(stderr_b.decode("utf-8", "replace"), max_chars)

        parts: list[str] = []
        if stdout.strip():
            parts.append(f"stdout:\n{stdout.rstrip()}")
        if stderr.strip():
            parts.append(f"stderr:\n{stderr.rstrip()}")
        if proc.returncode != 0:
            parts.append(f"exit code: {proc.returncode}")

        created = [f for f in _snapshot(workdir) - pre_existing][:_MAX_REPORTED_FILES]
        for rel in sorted(created):
            path = os.path.join(workdir, rel)
            size = os.path.getsize(path)
            preview = _read_text_preview(path)
            if preview is not None:
                parts.append(f"file created: {rel} ({size} bytes)\n{preview}")
            else:
                parts.append(f"file created: {rel} ({size} bytes, binary — not returned inline)")

        return "\n\n".join(parts) if parts else "(no output)"
    except (OSError, ValueError) as exc:
        logger.warning("python_exec failed: %s", exc)
        return f"Error: {exc}"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _snapshot(workdir: str) -> set:
    """Relative paths of every regular file under workdir (except the script)."""
    out = set()
    for root, _dirs, names in os.walk(workdir):
        for name in names:
            rel = os.path.relpath(os.path.join(root, name), workdir)
            if rel != "__main__.py":
                out.add(rel)
    return out


def _read_text_preview(path: str) -> str | None:
    """Return the file content if it decodes as text, capped; None for binary."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(_INLINE_FILE_MAX + 1)
        if b"\x00" in raw:
            return None
        text = raw.decode("utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    if len(text) > _INLINE_FILE_MAX:
        text = text[:_INLINE_FILE_MAX] + "\n[Truncated]"
    return text


def _truncate(text: str, limit: int) -> str:
    if limit and len(text) > limit:
        return text[:limit] + f"\n[Truncated — {len(text) - limit} chars omitted]"
    return text
