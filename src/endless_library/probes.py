"""Source / mirror health probes. Mostly HTTP HEAD with timing + status."""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    url: str
    ok: bool
    status: int | None
    latency_ms: int | None
    error: str | None = None


def _is_cf_walled_host(url: str) -> bool:
    """True if the host needs FlareSolverr — vanilla httpx will see 403."""
    return "welib.org" in url


def probe_http(url: str, *, timeout: float = 8.0) -> ProbeResult:
    """HEAD probe with timing. Falls back to GET if the server rejects HEAD."""
    if _is_cf_walled_host(url):
        return _probe_via_flaresolverr(url, timeout=timeout)
    t0 = time.monotonic()
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "endless-library-probe/0.1"},
        ) as c:
            r = c.head(url)
            if r.status_code in (405, 501):
                # Server doesn't support HEAD; try a tiny GET with Range header
                r = c.get(url, headers={"Range": "bytes=0-512"})
            ms = int((time.monotonic() - t0) * 1000)
            return ProbeResult(url=url, ok=r.status_code < 400, status=r.status_code, latency_ms=ms)
    except httpx.HTTPError as e:
        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(
            url=url, ok=False, status=None, latency_ms=ms, error=f"{type(e).__name__}: {e}"
        )
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(
            url=url, ok=False, status=None, latency_ms=ms, error=f"{type(e).__name__}: {e}"
        )


def probe_tcp(host: str, port: int, *, timeout: float = 5.0) -> ProbeResult:
    """Plain TCP connect, used for SMTP and other non-HTTP ports."""
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ms = int((time.monotonic() - t0) * 1000)
            return ProbeResult(url=f"{host}:{port}", ok=True, status=200, latency_ms=ms)
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(
            url=f"{host}:{port}",
            ok=False,
            status=None,
            latency_ms=ms,
            error=f"{type(e).__name__}: {e}",
        )


def _probe_via_flaresolverr(url: str, *, timeout: float) -> ProbeResult:
    """Probe a CF-walled mirror through FlareSolverr so we don't false-positive
    on a 403 that's just Cloudflare's anti-bot wall. If FS itself is
    unreachable, return a 0-status result with 'flaresolverr unavailable' so
    the user can fix the upstream rather than thinking welib is dead.
    """
    import os
    import time as _time

    from endless_library.flaresolverr import FlareSolverr, FlareSolverrError

    started = _time.monotonic()
    fs_url = os.environ.get("FLARESOLVERR_URL") or "http://127.0.0.1:8191/v1"
    fs = FlareSolverr(fs_url, max_timeout_ms=int(timeout * 1000))
    try:
        r = fs.get(url)
    except FlareSolverrError as e:
        ms = int((_time.monotonic() - started) * 1000)
        return ProbeResult(
            url=url,
            ok=False,
            status=None,
            latency_ms=ms,
            error=f"flaresolverr unavailable: {e}",
        )
    except Exception as e:
        ms = int((_time.monotonic() - started) * 1000)
        return ProbeResult(
            url=url,
            ok=False,
            status=None,
            latency_ms=ms,
            error=f"probe-via-FS crashed: {e}",
        )
    ms = int((_time.monotonic() - started) * 1000)
    code = int(r.status_code or 0)
    return ProbeResult(url=url, ok=code < 400, status=code, latency_ms=ms)


CURATED_MIRRORS: tuple[dict, ...] = (
    {"kind": "annas", "url": "https://annas-archive.gl", "label": "Anna's Archive (.gl)"},
    {"kind": "annas", "url": "https://annas-archive.pk", "label": "Anna's Archive (.pk)"},
    {"kind": "annas", "url": "https://annas-archive.gd", "label": "Anna's Archive (.gd)"},
    {"kind": "welib", "url": "https://welib.org", "label": "Welib"},
    # LibGen rotation per shadowlibraries.github.io/DirectDownloads/libgen
    # — .is/.rs/.st have been flaky for over a year; replaced with the live set.
    {"kind": "libgen", "url": "https://libgen.li", "label": "LibGen.li"},
    {"kind": "libgen", "url": "https://libgen.gl", "label": "LibGen.gl"},
    {"kind": "libgen", "url": "https://libgen.la", "label": "LibGen.la"},
    {"kind": "libgen", "url": "https://libgen.vg", "label": "LibGen.vg"},
    {"kind": "libgen", "url": "https://libgen.bz", "label": "LibGen.bz"},
)


# Historical defaults we want auto-disabled (NOT deleted — user may have
# re-enabled one knowingly). Anything in CURATED_MIRRORS overrides this.
LEGACY_CURATED: tuple[str, ...] = (
    "https://libgen.is",
    "https://libgen.rs",
    "https://libgen.st",
)


def probe_curated() -> list[tuple[dict, ProbeResult]]:
    """Probe every curated mirror once. Returns list of (entry, result)."""
    out: list[tuple[dict, ProbeResult]] = []
    for entry in CURATED_MIRRORS:
        result = probe_http(entry["url"])
        out.append((entry, result))
    return out


def host(url: str) -> str:
    return urlparse(url).netloc
