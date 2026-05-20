"""Wikisource via the Wikimedia ws-export Toolforge service.

Map a book title -> Wikisource page via Wikidata's SPARQL endpoint
(property P1733 = Wikisource page); then build the ws-export URL
that produces the EPUB. Fallback for non-English public-domain
works that Gutenberg lacks.

Phase 6s.1.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery

log = logging.getLogger(__name__)

SPARQL_TEMPLATE = (
    "SELECT ?work ?wikisourcePage WHERE {{ "
    '  ?work ?label "{title}"@{lang}. '
    "  ?work wdt:P1733 ?wikisourcePage. "
    "}} LIMIT 5"
)

WS_EXPORT = "https://ws-export.wmcloud.org/"


class Wikisource:
    name = "wikisource"

    def __init__(self, cfg, http_get=None) -> None:
        self._cfg = cfg

    def _sparql_pages(self, title: str, lang: str) -> list[str]:
        safe_title = title.replace('"', "'")
        query = SPARQL_TEMPLATE.format(title=safe_title, lang=lang)
        try:
            r = httpx.get(
                "https://query.wikidata.org/sparql",
                params={"query": query, "format": "json"},
                timeout=15.0,
                headers={
                    "User-Agent": "endless-library/0.1",
                    "Accept": "application/sparql-results+json",
                },
            )
            if r.status_code != 200:
                return []
            bindings = r.json().get("results", {}).get("bindings", [])
            return [b["wikisourcePage"]["value"] for b in bindings if "wikisourcePage" in b]
        except (httpx.HTTPError, ValueError) as e:
            log.info("wikisource sparql: %s", e)
            return []

    def search(self, sq: SearchQuery) -> list[Candidate]:
        if not sq.title:
            return []
        out: list[Candidate] = []
        for lang in ("en", "fr", "de", "es", "ru"):
            pages = self._sparql_pages(sq.title, lang)
            for page_url in pages:
                page_title = page_url.rsplit("/wiki/", 1)[-1]
                wsx_url = f"{WS_EXPORT}?lang={lang}&page={quote(page_title)}&format=epub"
                out.append(
                    Candidate(
                        provider="wikisource",
                        md5=None,
                        title=sq.title,
                        author=sq.author or "",
                        language=lang,
                        format="epub",
                        filesize_bytes=None,
                        year=None,
                        publisher="Wikisource",
                        edition_hints="",
                        detail_url=wsx_url,
                    )
                )
                if len(out) >= 5:
                    return out
            if out:
                break
        return out

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        if not candidate.detail_url:
            return None
        return DownloadHandle(url=candidate.detail_url, headers={})
