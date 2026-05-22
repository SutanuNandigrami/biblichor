"""Mediafire dynamic URL resolver (Phase 6w.5a).

Mediafire serves the real download link inside a JavaScript block:
    window.location.href = "https://download...mediafire.com/file/...";

This module fetches the page and extracts that URL via regex, avoiding
the need for a headless browser.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# Matches:  window.location.href = "https://..."
_HREF_RE = re.compile(
    r"""window\.location\.href\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)


def resolve(url: str, session) -> str | None:
    """Resolve a Mediafire share URL to a direct download URL.

    Parameters
    ----------
    url:
        A Mediafire share page URL, e.g.
        ``https://www.mediafire.com/file/abc123/book.epub/file``
    session:
        An httpx-compatible client (supports ``.get(url, ...)``) that
        already has any required headers / cookies.

    Returns the direct download URL, or ``None`` if resolution fails.
    """
    try:
        r = session.get(url, follow_redirects=True)
    except Exception as e:
        log.warning("mediafire_helpers.resolve: GET %s failed: %s", url, e)
        return None

    if r.status_code != 200:
        log.warning("mediafire_helpers.resolve: HTTP %s for %s", r.status_code, url)
        return None

    m = _HREF_RE.search(r.text)
    if not m:
        log.debug("mediafire_helpers.resolve: no window.location.href found in %s", url)
        return None

    direct = m.group(1)
    log.debug("mediafire_helpers.resolve: %s -> %s", url, direct)
    return direct
