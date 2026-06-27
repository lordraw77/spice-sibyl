"""
Prometheus metrics (Phase 16).

Defines the application's metric collectors as module-level singletons and a
``render_metrics`` helper for the ``GET /metrics`` endpoint.  Instrumentation
points:
  * HTTP layer — the request-context middleware records ``sibyl_http_requests_total``
    and ``sibyl_http_request_duration_seconds``.
  * Chat streaming — ``ChatService.stream`` adjusts ``sibyl_active_sse_streams`` and,
    on the final meta frame, records per-provider request/token/latency series.
"""

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# ── HTTP layer ───────────────────────────────────────────────────────────────
http_requests_total = Counter(
    "sibyl_http_requests_total",
    "Total HTTP requests handled.",
    ["method", "path", "status"],
)
http_request_duration_seconds = Histogram(
    "sibyl_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["method", "path"],
)

# ── Provider / chat layer ────────────────────────────────────────────────────
provider_requests_total = Counter(
    "sibyl_provider_requests_total",
    "Chat completion requests per provider.",
    ["provider", "model", "status"],
)
provider_tokens_total = Counter(
    "sibyl_provider_tokens_total",
    "Tokens processed per provider, split by kind (prompt/completion).",
    ["provider", "model", "kind"],
)
provider_latency_seconds = Histogram(
    "sibyl_provider_latency_seconds",
    "Chat completion latency per provider, in seconds.",
    ["provider", "model"],
)
active_sse_streams = Gauge(
    "sibyl_active_sse_streams",
    "Number of in-flight streaming chat responses.",
)


def render_metrics() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST
