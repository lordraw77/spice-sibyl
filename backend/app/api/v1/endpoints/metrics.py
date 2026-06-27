"""
Prometheus metrics endpoint (Phase 16).

Exposes ``GET /v1/metrics`` in the OpenMetrics text format for scraping.  Kept
out of the JWT/rate-limit guard so a Prometheus server can scrape it; when
``settings.metrics_token`` is set, a matching ``Authorization: Bearer`` header is
required.
"""

from fastapi import APIRouter, Header, HTTPException, Response

from app.core import metrics
from app.core.config import settings

router = APIRouter()


@router.get("")
async def prometheus_metrics(authorization: str | None = Header(default=None)):
    if settings.metrics_token:
        expected = f"Bearer {settings.metrics_token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="Invalid metrics token")
    body, content_type = metrics.render_metrics()
    return Response(content=body, media_type=content_type)
