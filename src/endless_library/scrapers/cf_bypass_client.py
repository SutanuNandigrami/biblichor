"""Thin HTTP wrapper around the sarperavci CloudflareBypassForScraping
sidecar (compose service `cf-bypass`). POST a URL, get resolved HTML
back. Sidecar internally drives DrissionPage / patched Chromium and
handles Cloudflare interactive challenges that defeat curl-cffi and
FlareSolverr alike.

Phase 6w.2: uses plain httpx (NOT curl-cffi) — we don't need TLS
fingerprint tricks to talk to our own sidecar on the biblichor network.
Phase 6w ultrareview C6: SSRF guard added.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

# Docker-network service names that must never be proxied through the
# cf-bypass sidecar (SSRF: sidecar lives on the biblichor network and
# could otherwise reach internal services).
_BLOCKED_NETLOCS = frozenset({
    "biblichor",
    "bookorbit",
    "bookorbit-db",
    "flaresolverr",
    "cf-bypass",
    "tor",
    "slskd",
    "clamav",
    "biblichor-clamav",
})


def _is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    netloc = (parsed.hostname or "").lower()
    if not netloc:
        return False
    if netloc in _BLOCKED_NETLOCS:
        return False
    if netloc == "localhost" or netloc.startswith("127.") or netloc == "::1":
        return False
    if netloc.startswith("10.") or netloc.startswith("192.168."):
        return False
    return True


def resolve(url: str, *, timeout: float = 90.0) -> str:
    """POST `url` to the sidecar; return its resolved HTML.
    Raises ValueError on unsafe URLs.
    Raises httpx.HTTPError on transport failure / non-2xx.
    """
    if not _is_safe_url(url):
        raise ValueError(f"refusing to proxy untrusted URL through cf-bypass: {url!r}")
    base = os.environ.get("CF_BYPASS_URL", "http://cf-bypass:8000")
    r = httpx.post(
        f"{base.rstrip('/')}/cf-clearance-scraper",
        json={"url": url},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["html"]
