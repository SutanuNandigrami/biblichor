"""Standard Ebooks — hand-curated public-domain EPUBs.

Scrapes the public OPDS Atom feed at /opds/all and fuzzy-matches
title + author. detail_url is the per-book EPUB; resolve_cdn is
a no-op wrapper.

Phase 6s.1.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx

from endless_library.scrapers.http_client import BIBLICHOR_USER_AGENT
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery

log = logging.getLogger(__name__)

BASE = "https://standardebooks.org"
FEED_URL = f"{BASE}/opds/all"


class StandardEbooks:
    name = "standard_ebooks"

    def __init__(self, cfg, http_get=None) -> None:
        self._cfg = cfg
        self._http_get = http_get
        self._cached_feed: str | None = None

    def _fetch_feed(self) -> str:
        if self._cached_feed is not None:
            return self._cached_feed
        try:
            r = httpx.get(
                FEED_URL,
                timeout=30.0,
                headers={"User-Agent": BIBLICHOR_USER_AGENT},
            )
        except httpx.HTTPError as e:
            log.info("standard_ebooks: %s", e)
            return ""
        if r.status_code != 200:
            return ""
        self._cached_feed = r.text
        return r.text

    def search(self, sq: SearchQuery) -> list[Candidate]:
        if not sq.title:
            return []
        feed = self._fetch_feed()
        if not feed:
            return []
        soup = BeautifulSoup(feed, "xml")
        out: list[Candidate] = []
        title_q = sq.title.lower()
        author_q = (sq.author or "").lower()
        for entry in soup.find_all("entry"):
            title_el = entry.find("title")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            author_names = [
                a.find("name").get_text(strip=True)
                for a in entry.find_all("author")
                if a.find("name") is not None
            ]
            author = ", ".join(author_names)
            score = fuzz.token_set_ratio(title.lower(), title_q)
            if score < 80 and title_q not in title.lower():
                continue
            if author_q and author and fuzz.token_set_ratio(author.lower(), author_q) < 60:
                continue
            link = entry.find(
                "link",
                attrs={
                    "rel": "http://opds-spec.org/acquisition",
                    "type": "application/epub+zip",
                },
            )
            if link is None:
                continue
            url = urljoin(BASE, link.get("href", ""))
            lang = "en"
            dc_lang = entry.find("language")
            if dc_lang:
                lang = (dc_lang.get_text(strip=True) or "en").split("-")[0]
            out.append(
                Candidate(
                    provider="standard_ebooks",
                    md5=None,
                    title=title,
                    author=author,
                    language=lang,
                    format="epub",
                    filesize_bytes=None,
                    year=None,
                    publisher="Standard Ebooks",
                    edition_hints="hand-typeset",
                    detail_url=url,
                )
            )
            if len(out) >= 5:
                break
        return out

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        if not candidate.detail_url:
            return None
        return DownloadHandle(url=candidate.detail_url, headers={})
