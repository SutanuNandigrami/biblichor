"""Auto-discover Anna's Archive mirrors from Wikipedia's infobox.

We hardcode three mirrors in config.yaml (annas-archive.gl/pk/gd) because
those are the ones the project officially advertises. Anna's adds and
retires mirrors as registrars/hosts get pressured, so the hardcoded list
goes stale. Wikipedia's Anna's Archive page keeps an up-to-date list in
its infobox `vcard` table — we fetch it every 6h and merge any new domains
into the in-memory `cfg.scrapers.annas_mirrors` so future scraper builds
see them.

Failure modes are quiet on purpose: any fetch/parse error falls back to
the cached list, and an empty cache falls back to the configured list.
The hardcoded mirrors are always present.

Adapted from zelestcarlyone/stacks (utils/domainupdater.py), trimmed to
the parts we actually use.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/Anna%27s_Archive"
DEFAULT_UPDATE_INTERVAL_SECONDS = 6 * 3600
CACHE_FILENAME = "wiki_annas_domains.json"

# M4: parse_domains_from_html now uses lxml instead of raw-byte regex
# (lxml handles malformed HTML correctly and avoids regex fragility).


def parse_domains_from_html(html: bytes | str) -> list[str]:
    """Pull bare domains (annas-archive.gl etc.) from the Wikipedia infobox.

    Returns [] if the infobox can't be located. Returns full URLs lowercased
    to bare host (no scheme, no path).

    M4: switched from raw-byte regex to lxml for correct handling of
    malformed HTML and Unicode entity decoding.
    """
    from lxml import etree  # deferred; only needed during wiki refresh

    html_str = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html

    try:
        tree = etree.fromstring(html_str.encode("utf-8"), etree.HTMLParser())
    except Exception:
        return []

    # Find the infobox vcard table
    tables = tree.xpath(
        '//table[contains(concat(" ", normalize-space(@class), " "), " infobox ")]'
        '[contains(concat(" ", normalize-space(@class), " "), " vcard ")]'
    )
    if not tables:
        return []

    out: list[str] = []
    for table in tables:
        # Find spans with class word "url" (word-boundary via concat trick
        # to avoid matching "sourceurl", "urlbar", etc.) — I-NEW-1
        for span in table.xpath(
            './/span[contains(concat(" ", normalize-space(@class), " "), " url ")]'
        ):
            # Find external links within the span
            for a in span.xpath('.//a[contains(@class, "external")][@href]'):
                href_str = (a.get("href") or "").strip()
                if not href_str:
                    continue
                if "://" not in href_str:
                    href_str = "https:" + href_str
                parsed = urlparse(href_str)
                if parsed.netloc:
                    out.append(parsed.netloc.lower())

    # Dedup preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for d in out:
        if d not in seen:
            seen.add(d)
            deduped.append(d)
    return deduped


def fetch_wiki_domains(
    *,
    http_get: Callable[[str], tuple[int, bytes]] | None = None,
    timeout: float = 10.0,
) -> list[str]:
    """Fetch and parse Anna's Archive Wikipedia infobox.

    `http_get` is an optional dependency-injected callable returning
    (status_code, body_bytes) — used by tests so we don't hit the network.
    """
    try:
        if http_get is not None:
            status, body = http_get(WIKIPEDIA_URL)
        else:
            r = httpx.get(
                WIKIPEDIA_URL,
                timeout=timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": "biblichor/1.0 (+https://github.com/SutanuNandigrami/biblichor)"
                },
            )
            status, body = r.status_code, r.content
    except Exception as e:
        log.warning("wiki domain fetch failed: %s", e)
        return []
    if status != 200:
        log.warning("wiki returned status %s", status)
        return []
    domains = parse_domains_from_html(body)
    if domains:
        log.info("wiki domain refresh: %d domain(s): %s", len(domains), domains)
    else:
        log.warning("wiki domain refresh: parsed 0 domains (page layout changed?)")
    return domains


def cache_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / CACHE_FILENAME


def read_cache(path: Path) -> tuple[list[str], float]:
    """Returns (domains, timestamp). ([], 0) when the cache is missing/malformed."""
    try:
        with path.open() as f:
            data = json.load(f)
        return list(data.get("domains") or []), float(data.get("timestamp") or 0)
    except FileNotFoundError:
        return [], 0
    except Exception as e:
        log.warning("wiki cache unreadable (%s): %s", path, e)
        return [], 0


def write_cache(path: Path, domains: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump({"domains": domains, "timestamp": time.time()}, f)
    tmp.replace(path)


def is_stale(timestamp: float, *, max_age_seconds: int = DEFAULT_UPDATE_INTERVAL_SECONDS) -> bool:
    if timestamp <= 0:
        return True
    return (time.time() - timestamp) > max_age_seconds


def update_cache_if_stale(
    cache_file: Path,
    *,
    http_get: Callable[[str], tuple[int, bytes]] | None = None,
    max_age_seconds: int = DEFAULT_UPDATE_INTERVAL_SECONDS,
) -> list[str]:
    """Refresh the wiki cache if stale; return whatever's in cache after."""
    cached, ts = read_cache(cache_file)
    if not is_stale(ts, max_age_seconds=max_age_seconds):
        return cached
    fresh = fetch_wiki_domains(http_get=http_get)
    if fresh:
        write_cache(cache_file, fresh)
        return fresh
    # Fetch failed: keep whatever we had (possibly stale) so we don't lose mirrors
    return cached


def _to_https_url(s: str) -> str:
    s = s.strip()
    if not s:
        return ""
    if "://" in s:
        return s.rstrip("/")
    return f"https://{s}".rstrip("/")


def effective_mirrors(configured: list[str], cached: list[str]) -> list[str]:
    """Merge configured (config.yaml) with cached (Wikipedia) mirrors.

    Configured ordering wins (those are user-authoritative). Cached entries
    not already present append at the end. Returns full https URLs, deduped
    case-insensitively on host.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in [*configured, *cached]:
        url = _to_https_url(raw)
        if not url:
            continue
        host = urlparse(url).netloc.lower()
        if host in seen:
            continue
        seen.add(host)
        out.append(url)
    return out


# ---------------------------------------------------------------------------
# Phase 6w.2: Mirror rotation with cool-down
# ---------------------------------------------------------------------------
# Mirrors rotated across active Anna's Archive domains.
# 2026-06-16 probe (PR #42):
#   gl, pk, gd       -- live, full /md5 + /slow_download paths work
#   li               -- removed: persistent SSL cert chain failure at
#                       45.33.83.100 (unable to get local issuer cert)
#   pm               -- removed: TLS disconnect mid-handshake at
#                       172.236.108.16
#   cc, is           -- not added: Anna's-branded landing pages that
#                       404 on real md5s, so search-only fronts not
#                       usable download mirrors
# Per-mirror probe also confirmed all live mirrors are DDoS-Guard-
# gated on /slow_download, so rotation does NOT bypass DDG -- only
# helps when one mirror goes offline. On 5xx / connection-refused,
# cool that mirror for 5 minutes; pin success across calls when
# prefer_last_working is set.

_MIRRORS = (
    "annas-archive.gl",
    "annas-archive.pk",
    "annas-archive.gd",
)
_COOL_DOWN_SEC = 5 * 60

_state: dict[str, float] = {}  # mirror -> cool-until-epoch
_last_working: str | None = None
_STATE_LOCK = threading.Lock()


def _now() -> float:
    return time.time()


def _reset_state_for_tests() -> None:
    global _last_working
    with _STATE_LOCK:
        _state.clear()
        _last_working = None


def _is_cool(host: str) -> bool:
    until = _state.get(host)
    return until is not None and until > _now()


def next_mirror(prefer_last_working: bool = True) -> str:
    """Return a hostname currently usable. If prefer_last_working and
    the last-known-good is not cool, return it; else round-robin
    through non-cool mirrors. Falls back to earliest-expiring if all cool."""
    with _STATE_LOCK:
        if prefer_last_working and _last_working and not _is_cool(_last_working):
            return _last_working
        for m in _MIRRORS:
            if not _is_cool(m):
                return m
        # Everything cool — return the earliest expiring (least bad)
        return min(_MIRRORS, key=lambda m: _state.get(m, 0))


def mark_cool(host: str) -> None:
    with _STATE_LOCK:
        _state[host] = _now() + _COOL_DOWN_SEC


def mark_success(host: str) -> None:
    global _last_working
    with _STATE_LOCK:
        _last_working = host
        _state.pop(host, None)
