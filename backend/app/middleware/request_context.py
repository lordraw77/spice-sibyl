"""
Request-context middleware (Phase 16).

For every HTTP request it:
  * binds a ``request_id`` to the logging context (reusing an inbound
    ``X-Request-ID`` header when present, so an upstream proxy can set it),
  * echoes that id back on the response as ``X-Request-ID``,
  * records the HTTP request count and latency Prometheus series.

The templated route path (e.g. ``/api/v1/providers/{id}/test``) is used as the
metric label to keep cardinality bounded; it falls back to the raw path when the
route did not match.
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core import metrics
from app.core.logging_context import set_request_id


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        inbound = request.headers.get("x-request-id")
        request_id = set_request_id(inbound)

        request.state.request_id = request_id
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = request.scope.get("route")
            path = getattr(route, "path", None) or request.url.path
            metrics.http_requests_total.labels(
                method=request.method, path=path, status=str(status_code)
            ).inc()
            metrics.http_request_duration_seconds.labels(
                method=request.method, path=path
            ).observe(elapsed)
