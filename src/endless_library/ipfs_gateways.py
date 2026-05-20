"""Refreshable IPFS gateway list (Phase 6s.2).

Sourced from ipfs/public-gateway-checker on GitHub. Refreshed
daily by an APScheduler job; falls back to a hardcoded bootstrap
list if the refresh ever fails (so biblichor keeps working
offline / without a recent fetch).

Public API:
  refresh_gateway_list(db_path) -> int   # populates ipfs_gateways table
  list_gateways(db_path) -> list[str]    # current iteration order
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from endless_library.db.schema import connect

log = logging.getLogger(__name__)

# Bootstrap baseline (May 2026 — checker's "guaranteed-stable" anchors)
_BOOTSTRAP = (
    "https://ipfs.io",
    "https://dweb.link",
    "https://trustless-gateway.link",
    "https://cloudflare-ipfs.com",
    "https://gateway.pinata.cloud",
    "https://nftstorage.link",
    "https://w3s.link",
)

MANIFEST_URL = "https://raw.githubusercontent.com/ipfs/public-gateway-checker/main/gateways.json"


def refresh_gateway_list(db_path: Path) -> int:
    """Fetch the manifest, upsert into ipfs_gateways. Returns count.
    Silent on network failure — caller can detect via count==0."""
    try:
        r = httpx.get(
            MANIFEST_URL,
            timeout=15.0,
            headers={"User-Agent": "endless-library/0.1"},
        )
        if r.status_code != 200:
            log.info("ipfs gateway refresh: HTTP %s", r.status_code)
            return 0
        urls = r.json()
        if not isinstance(urls, list):
            return 0
    except (httpx.HTTPError, ValueError) as e:
        log.info("ipfs gateway refresh: %s", e)
        return 0

    now = int(time.time())
    upserted = 0
    with connect(db_path) as conn:
        for url in urls:
            if not isinstance(url, str) or not url.startswith("http"):
                continue
            conn.execute(
                """INSERT INTO ipfs_gateways (url, origin_isolation, last_check)
                   VALUES (?, 1, ?)
                   ON CONFLICT(url) DO UPDATE SET last_check=excluded.last_check""",
                (url, now),
            )
            upserted += 1
        conn.commit()
    return upserted


def list_gateways(db_path: Path | None) -> list[str]:
    """Return the current gateway list. Prefers the persisted list
    from the refresh job; falls back to bootstrap when empty or
    when db_path is None (testing / no scheduler yet)."""
    if db_path is None:
        return list(_BOOTSTRAP)
    try:
        with connect(db_path) as conn:
            rows = conn.execute(
                "SELECT url FROM ipfs_gateways ORDER BY (last_ok IS NULL), last_ok DESC"
            ).fetchall()
    except Exception:
        return list(_BOOTSTRAP)
    if not rows:
        return list(_BOOTSTRAP)
    return [r[0] for r in rows]
