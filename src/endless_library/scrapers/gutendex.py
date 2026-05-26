"""Project Gutenberg via Gutendex (https://gutendex.com).

A JSON wrapper around the nightly Project Gutenberg catalog.
No auth, no rate limit beyond polite usage. Returns books with
direct download URLs in multiple formats — detail_url IS the
CDN URL so resolve_cdn is a no-op wrapper.

Phase 6s.1.
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath
from urllib.parse import urlparse

import httpx

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.scrapers.http_client import BIBLICHOR_USER_AGENT

log = logging.getLogger(__name__)

API_BASE = "https://gutendex.com"


class Gutendex:
    name = "gutendex"

    def __init__(self, cfg, http_get=None) -> None:
        self._cfg = cfg
        self._http_get = http_get

    def search(self, sq: SearchQuery) -> list[Candidate]:
        if not sq.title:
            return []
        q = sq.title if not sq.author else f"{sq.title} {sq.author}"
        try:
            r = httpx.get(
                f"{API_BASE}/books",
                params={"search": q},
                timeout=15.0,
                headers={"User-Agent": BIBLICHOR_USER_AGENT},
            )
        except httpx.HTTPError as e:
            log.info("gutendex: %s", e)
            return []
        if r.status_code != 200:
            return []
        results = r.json().get("results", [])
        out: list[Candidate] = []
        format_pref = (
            "application/epub+zip",
            "application/x-mobipocket-ebook",
            "text/plain; charset=utf-8",
            "text/plain",
        )
        for book in results[:25]:
            fmts = book.get("formats", {})
            url = None
            chosen_fmt = None
            for fmt in format_pref:
                if fmt in fmts:
                    url = fmts[fmt]
                    chosen_fmt = fmt
                    break
            if not url:
                continue
            ext = PurePosixPath(urlparse(url).path).suffix.lstrip(".").lower()
            if chosen_fmt and chosen_fmt.startswith("application/epub"):
                ext = "epub"
            elif chosen_fmt and "mobi" in chosen_fmt:
                ext = "mobi"
            elif not ext:
                ext = "txt"
            authors = ", ".join(a.get("name", "") for a in book.get("authors", []))
            out.append(
                Candidate(
                    provider="gutendex",
                    md5=None,
                    title=book.get("title", ""),
                    author=authors,
                    language=(book.get("languages") or ["en"])[0],
                    format=ext,
                    filesize_bytes=None,
                    year=None,
                    publisher=None,
                    edition_hints="",
                    detail_url=url,
                    raw={"gutenberg_id": book.get("id")},
                )
            )
        return out

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        if not candidate.detail_url:
            return None
        return DownloadHandle(url=candidate.detail_url, headers={})
