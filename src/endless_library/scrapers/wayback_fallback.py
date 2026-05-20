"""Wayback Machine CDX fallback for dead Anna's Archive pages.

When the live Anna's mirror chain returns 404 for a known MD5,
we query the CDX server for the last good snapshots of that
md5-page and extract IPFS CIDs / slow-server URLs from the
archived HTML.

Phase 6s.2.
"""

from __future__ import annotations

import logging
import re

import httpx

from endless_library.domain.models import DownloadHandle

log = logging.getLogger(__name__)

CDX_URL = "https://web.archive.org/cdx/search/cdx"
IPFS_CID_RE = re.compile(r"(?:ipfs[:/](?://)?)(Qm[A-Za-z0-9]{40,46}|bafy[a-z0-9]+)")


def recover_links(md5: str, *, limit: int = 5) -> list[DownloadHandle]:
    """Return DownloadHandles extracted from the last `limit` Wayback
    snapshots of the Anna's md5 page. Empty on any failure (caller
    falls through to the next strategy).
    """
    if not md5:
        return []
    try:
        r = httpx.get(
            CDX_URL,
            params={
                "url": f"annas-archive.org/md5/{md5}",
                "output": "json",
                "limit": f"-{limit}",
            },
            timeout=10.0,
            headers={"User-Agent": "endless-library/0.1"},
        )
        if r.status_code != 200:
            return []
        rows = r.json()
        if not isinstance(rows, list) or len(rows) < 2:
            return []
        rows = rows[1:]  # first row is the header
    except (httpx.HTTPError, ValueError) as e:
        log.info("wayback CDX: %s", e)
        return []

    out: list[DownloadHandle] = []
    seen_cids: set[str] = set()
    for row in rows:
        if len(row) < 3:
            continue
        timestamp, original = row[1], row[2]
        try:
            arch = httpx.get(
                f"https://web.archive.org/web/{timestamp}/{original}",
                timeout=15.0,
                headers={"User-Agent": "endless-library/0.1"},
            )
            if arch.status_code != 200:
                continue
            for m in IPFS_CID_RE.finditer(arch.text):
                cid = m.group(1)
                if cid in seen_cids:
                    continue
                seen_cids.add(cid)
                out.append(
                    DownloadHandle(
                        url=f"https://ipfs.io/ipfs/{cid}",
                        headers={},
                    )
                )
        except httpx.HTTPError:
            continue
    return out
