"""Thin HTTP wrapper around the sarperavci CloudflareBypassForScraping
sidecar (compose service `cf-bypass`). POST a URL, get resolved HTML
back. Sidecar internally drives DrissionPage / patched Chromium and
handles Cloudflare interactive challenges that defeat curl-cffi and
FlareSolverr alike.

Phase 6w.2: uses plain httpx (NOT curl-cffi) — we don't need TLS
fingerprint tricks to talk to our own sidecar on the biblichor network.
"""
from __future__ import annotations

import os

import httpx


def resolve(url: str, *, timeout: float = 90.0) -> str:
    """POST `url` to the sidecar; return its resolved HTML.
    Raises httpx.HTTPError on transport failure / non-2xx.
    """
    base = os.environ.get("CF_BYPASS_URL", "http://cf-bypass:8000")
    r = httpx.post(
        f"{base.rstrip('/')}/cf-clearance-scraper",
        json={"url": url},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["html"]
