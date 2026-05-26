"""Thin HTTP wrapper around the sarperavci CloudflareBypassForScraping
sidecar (compose service ). GET a URL via the sidecar's /html
endpoint, get resolved HTML back. Sidecar internally drives DrissionPage /
patched Chromium and handles Cloudflare interactive challenges that defeat
curl-cffi and FlareSolverr alike.

Phase 6w.2: uses plain httpx (NOT curl-cffi) — we don't need TLS
fingerprint tricks to talk to our own sidecar on the biblichor network.
Phase 6w ultrareview C6: SSRF guard added.
"""

from __future__ import annotations

import logging
import os
import random
import time
from urllib.parse import urlparse

import httpx

from endless_library.url_safety import UnsafeUrlError, assert_safe_url

log = logging.getLogger(__name__)

# Docker-network service names that must never be proxied through the
# cf-bypass sidecar (SSRF: sidecar lives on the biblichor network and
# could otherwise reach internal services). These are checked IN ADDITION
# to the canonical assert_safe_url which covers all RFC1918/link-local
# ranges, cloud metadata endpoints, .local/.internal TLDs, and DNS
# resolution (C-NEW-3 second pass).
_BLOCKED_NETLOCS = frozenset(
    {
        "biblichor",
        "bookorbit",
        "bookorbit-db",
        "flaresolverr",
        "cf-bypass",
        "tor",
        "slskd",
        "clamav",
        "biblichor-clamav",
    }
)


def resolve(url: str, *, timeout: float = 90.0) -> str:
    """GET `url` via the sidecar's /html endpoint; return its resolved HTML.
    Raises ValueError on unsafe URLs.
    Raises httpx.HTTPError on transport failure / non-2xx.
    """
    # C-NEW-3: use canonical assert_safe_url (covers 169.254/16, 172.16/12,
    # 100.64/10, IPv6 link-local, .internal/.local TLDs, DNS resolution).
    try:
        assert_safe_url(url)
    except UnsafeUrlError as e:
        raise ValueError(f"refusing to proxy untrusted URL: {e}") from e
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() in _BLOCKED_NETLOCS:
        raise ValueError(f"refusing to proxy to docker service: {url!r}")
    base = os.environ.get("CF_BYPASS_URL", "http://cf-bypass:8000")
    # M13: single retry with 4-6s jittered backoff on transport errors.
    for attempt in range(2):
        try:
            r = httpx.get(
                f"{base.rstrip('/')}/html",
                params={"url": url},
                timeout=timeout,
            )
            r.raise_for_status()
            return r.text
        except httpx.HTTPError as e:
            if attempt == 0:
                log.info("cf_bypass: retrying %s after %s", url, e)
                time.sleep(5 + random.uniform(-1, 1))
            else:
                raise
