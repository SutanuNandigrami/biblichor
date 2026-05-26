"""Mobilism EN books scraper (Phase 6w.5c).

Searches Mobilism's English Books subforum (forum_id=15) for threads
matching the query title. Opens each thread's first post, extracts
Mediafire download links, and resolves them to direct download URLs
via ``mediafire_helpers.resolve()``.

Authentication is handled by ``MobilismSession``; credentials come from
ScrapersCfg (``mobilism_username`` / ``mobilism_password``).

Candidate.provider is ``"mobilism_books"``.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.scrapers.mediafire_helpers import resolve
from endless_library.scrapers.mobilism import AuthFailed, MobilismSession, NotConfigured

log = logging.getLogger(__name__)

_FORUM_BASE = "https://forum.mobilism.org/"
_SEARCH_URL = (
    "https://forum.mobilism.org/search.php?keywords={query}&fid%5B%5D=15&sr=topics&sf=titleonly"
)

# Extension clues from thread title / post text
_EXT_RE = re.compile(r"\b(epub|mobi|azw3|pdf|lit|djvu)\b", re.IGNORECASE)


# M6: Configuration-drift probe: a search for this common term should
# ALWAYS return results if forum_id=15 is valid. 0 results means the
# subforum was restructured and forum_id needs updating.
_DRIFT_PROBE_TERM = "the"

# Mediafire share-page pattern
_MF_LINK_RE = re.compile(
    r"https?://(?:www\.)?mediafire\.com/file/[^\s\"'<>]+",
    re.IGNORECASE,
)

# I-NEW-2: Rate-limit the drift probe (once per 6 hours max).
_DRIFT_PROBE_TTL = 6 * 3600
_drift_probe_last_at: float = 0.0
_drift_probe_lock = threading.Lock()


def _check_drift(session) -> None:
    """Fire a drift probe at most once per _DRIFT_PROBE_TTL seconds (I-NEW-2).

    Checks whether forum_id=15 returns results for a known-good probe term.
    Rate-limited: rapid empty-result bursts do not hammer the forum.
    Lock ensures only one concurrent caller runs the probe (M-3rd-2).
    """
    with _drift_probe_lock:
        global _drift_probe_last_at
        if time.time() - _drift_probe_last_at < _DRIFT_PROBE_TTL:
            log.debug(
                "mobilism_books: drift probe skipped (last %.0fs ago)",
                time.time() - _drift_probe_last_at,
            )
            return
        _drift_probe_last_at = time.time()  # claim slot before request
        probe_url = _SEARCH_URL.format(query=quote_plus(_DRIFT_PROBE_TERM))
        try:
            probe_resp = session.get(probe_url, follow_redirects=True)
            probe_soup = BeautifulSoup(probe_resp.text, "lxml")
            if probe_resp.status_code == 200 and not probe_soup.select("a.topictitle"):
                log.warning(
                    "mobilism_books: forum_id=15 drift detected — "
                    "probe query %r returned 0 threads; subforum may have moved",
                    _DRIFT_PROBE_TERM,
                )
        except Exception as _e:
            log.debug("mobilism_books: drift probe failed: %s", _e)


def _ext_from_text(text: str) -> str | None:
    m = _EXT_RE.search(text)
    return m.group(1).lower() if m else None


class MobilismBooks:
    """Search Mobilism English Books subforum and resolve Mediafire links."""

    name = "mobilism_books"
    provider = "mobilism_books"
    category = "commercial"

    def __init__(self, cfg, **kw) -> None:
        self._cfg = cfg

    def search(self, query: SearchQuery) -> list[Candidate]:
        try:
            session = MobilismSession.get(self._cfg)
        except NotConfigured:
            log.info("mobilism_books: not configured (no credentials)")
            return []
        except AuthFailed as e:
            log.warning("mobilism_books: auth failed: %s", e)
            return []

        search_query = query.title or ""
        if not search_query:
            return []

        url = _SEARCH_URL.format(query=quote_plus(search_query))
        try:
            resp = session.get(url, follow_redirects=True)
        except Exception as e:
            log.warning("mobilism_books: search GET failed: %s", e)
            return []

        if resp.status_code != 200:
            log.warning("mobilism_books: search returned HTTP %s", resp.status_code)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        thread_links = self._extract_thread_links(soup)
        if not thread_links:
            log.debug("mobilism_books: no threads found for %r", search_query)
            # M6: Check for forum_id drift when the real query also finds
            # nothing. Only probe when the query itself is non-trivial so
            # we don't false-alarm on very rare book titles.
            if search_query and search_query.lower() != _DRIFT_PROBE_TERM:
                _check_drift(session)
            return []

        candidates: list[Candidate] = []
        for thread_url, thread_title in thread_links[:5]:  # cap at 5 threads
            cands = self._scrape_thread(session, thread_url, thread_title, query)
            candidates.extend(cands)

        return candidates

    def _extract_thread_links(self, soup: BeautifulSoup) -> list[tuple[str, str]]:
        """Return list of (absolute_url, title) for each thread result."""
        links: list[tuple[str, str]] = []
        for a in soup.select("a.topictitle"):
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if href:
                abs_url = urljoin(_FORUM_BASE, href)
                links.append((abs_url, title))
        return links

    def _scrape_thread(
        self,
        session,
        thread_url: str,
        thread_title: str,
        query: SearchQuery,
    ) -> list[Candidate]:
        """Open a thread page and extract Mediafire download candidates."""
        try:
            resp = session.get(thread_url, follow_redirects=True)
        except Exception as e:
            log.debug("mobilism_books: thread GET failed %s: %s", thread_url, e)
            return []

        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        # The first post usually contains the download link(s)
        post_body = soup.select_one("div.postbody") or soup.select_one("div.content")
        if post_body is None:
            return []

        candidates: list[Candidate] = []
        for a in post_body.find_all("a", href=True):
            href = a["href"]
            if "mediafire.com/file" not in href:
                continue
            direct = resolve(href, session)
            if direct is None:
                direct = href  # keep the share-page URL as fallback detail_url

            fmt = _ext_from_text(thread_title + " " + a.get_text())
            candidates.append(
                Candidate(
                    provider="mobilism_books",
                    md5=None,
                    title=query.title,
                    author=query.author,
                    language=query.language,
                    format=fmt,
                    filesize_bytes=None,
                    year=None,
                    publisher=None,
                    edition_hints="",
                    detail_url=href,  # share-page URL (scoring uses this)
                    raw={
                        "direct_url": direct,
                        "thread_url": thread_url,
                        "thread_title": thread_title,
                    },
                )
            )

        return candidates

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        """Resolve the stored Mediafire share URL to a direct download handle."""
        direct = candidate.raw.get("direct_url")
        if not direct or direct == candidate.detail_url:
            # Need to re-resolve
            try:
                session = MobilismSession.get(self._cfg)
            except (NotConfigured, AuthFailed):
                return None
            direct = resolve(candidate.detail_url, session)
        if not direct:
            return None
        return DownloadHandle(url=direct, headers={})
