"""
Structured logging + request correlation (Phase 16).

A ContextVar carries a per-request ``request_id`` across the chat service,
providers, tools, and the Telegram flow without threading it through every call
signature.  ``JsonFormatter`` emits one JSON object per log record with the id
attached, so logs from a single request (web or Telegram) can be grepped by
``request_id`` end-to-end.

Set ``LOG_FORMAT=json`` to enable JSON output; any other value keeps the
human-readable text format used in local dev.
"""

import json
import logging
from contextvars import ContextVar
from uuid import uuid4

# Empty string means "no request in scope" (e.g. startup logs).
_request_id: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Return the request id bound to the current context (or '')."""
    return _request_id.get()


def set_request_id(request_id: str | None = None) -> str:
    """Bind a request id to the current context, generating one if absent."""
    rid = request_id or uuid4().hex
    _request_id.set(rid)
    return rid


def new_request_id() -> str:
    """Generate a fresh request id without binding it."""
    return uuid4().hex


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON with the request id attached."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": get_request_id() or None,
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)
