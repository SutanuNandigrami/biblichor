"""OAPEN + DOAB — open-access academic books.

Two complementary REST APIs returning JSON. Queries both and
merges dedup'd by DOI. Mostly PDF; some EPUB. No auth.

Phase 6s.1.
"""

from __future__ import annotations

import logging
from typing import Literal
from urllib.parse import urljoin

import httpx

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery

log = logging.getLogger(__name__)


def _query(url: str, q: str) -> list[dict]:
    try:
        r = httpx.get(
            url,
            params={"query": q, "expand": "metadata,bitstreams"},
            timeout=15.0,
            headers={
                "User-Agent": "endless-library/0.1",
                "Accept": "application/json",
            },
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                return data[:10]
    except (httpx.HTTPError, ValueError) as e:
        log.info("oapen_doab %s: %s", url, e)
    return []


def _build_candidate(rec: dict, provider: Literal["oapen", "doab"], base: str) -> Candidate | None:
    meta = {m["key"]: m["value"] for m in rec.get("metadata", [])}
    title = meta.get("dc.title", "")
    author = meta.get("dc.creator") or meta.get("dc.contributor.author") or ""
    bitstreams = rec.get("bitstreams") or []
    epub = next(
        (b for b in bitstreams if "epub" in (b.get("format") or "").lower()),
        None,
    )
    pdf = next(
        (b for b in bitstreams if "pdf" in (b.get("format") or "").lower()),
        None,
    )
    chosen = epub or pdf
    if not chosen or not title:
        return None
    url = urljoin(base, chosen.get("retrieveLink", ""))
    fmt = "epub" if epub else "pdf"
    return Candidate(
        provider=provider,
        md5=None,
        title=title,
        author=author,
        language=meta.get("dc.language", "en"),
        format=fmt,
        filesize_bytes=None,
        year=None,
        publisher=meta.get("dc.publisher"),
        edition_hints="open-access",
        detail_url=url,
    )


class OapenDoab:
    name = "oapen_doab"

    def __init__(self, cfg, http_get=None) -> None:
        self._cfg = cfg

    def search(self, sq: SearchQuery) -> list[Candidate]:
        if not sq.title:
            return []
        q = sq.title if not sq.author else f"{sq.title} {sq.author}"
        oapen_rs = _query("https://library.oapen.org/rest/search", q)
        doab_rs = _query("https://directory.doabooks.org/rest/search", q)
        out: list[Candidate] = []
        seen_dois: set[str] = set()
        for rec in oapen_rs:
            c = _build_candidate(rec, "oapen", "https://library.oapen.org")
            if c is None:
                continue
            doi = next(
                (m["value"] for m in rec.get("metadata", []) if m["key"] == "dc.identifier.doi"),
                "",
            )
            if doi and doi in seen_dois:
                continue
            seen_dois.add(doi)
            out.append(c)
        for rec in doab_rs:
            c = _build_candidate(rec, "doab", "https://directory.doabooks.org")
            if c is None:
                continue
            doi = next(
                (m["value"] for m in rec.get("metadata", []) if m["key"] == "dc.identifier.doi"),
                "",
            )
            if doi and doi in seen_dois:
                continue
            seen_dois.add(doi)
            out.append(c)
        return out[:10]

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        if not candidate.detail_url:
            return None
        return DownloadHandle(url=candidate.detail_url, headers={})
