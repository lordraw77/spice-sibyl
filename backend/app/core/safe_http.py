"""SSRF-safe HTTP helpers (audit findings 1.2 / 1.3).

`assert_public_url` alone only vets the URL the caller typed. Every client in
this codebase used to run with `follow_redirects=True`, so a vetted public host
could answer `302 Location: http://169.254.169.254/latest/meta-data/` and httpx
would dutifully fetch it — with whatever headers (and `$secrets`) the caller
attached. The guard has to be re-applied to *every* hop, which means following
redirects by hand.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Cap on hops. Deliberately small: legitimate APIs redirect once or twice, while
# a long chain is either a loop or someone probing for a validation gap.
MAX_REDIRECTS = 5


class BlockedURLError(Exception):
    """Raised when a URL — original or redirect target — must not be fetched."""


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


async def safe_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_redirects: int = MAX_REDIRECTS,
    **kwargs,
) -> httpx.Response:
    """Send a request, following redirects only through `assert_public_url`.

    The client **must** be built with `follow_redirects=False`, otherwise httpx
    resolves the chain internally and there is nothing left to validate. httpx
    builds each redirect request for us (`Response.next_request`), so method and
    body downgrades on 301/302/303 keep following the spec instead of a
    hand-rolled approximation of it.

    Raises `BlockedURLError` when any hop points somewhere it should not, or when
    the chain exceeds `max_redirects`.
    """
    blocked = assert_public_url(url)
    if blocked:
        raise BlockedURLError(blocked)

    request = client.build_request(method, url, **kwargs)
    for _ in range(max_redirects + 1):
        response = await client.send(request)
        next_request = response.next_request
        if next_request is None:
            return response

        # A redirect: vet the target before httpx is allowed anywhere near it.
        await response.aclose()
        target = str(next_request.url)
        blocked = assert_public_url(target)
        if blocked:
            logger.warning("SSRF guard: blocked redirect %s -> %s", url, target)
            raise BlockedURLError(f"{blocked} (redirected from {url})")
        request = next_request

    raise BlockedURLError(
        f"Error: too many redirects (>{max_redirects}) starting at {url}"
    )


async def safe_get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """`safe_request` with the method every caller here actually uses."""
    return await safe_request(client, "GET", url, **kwargs)
