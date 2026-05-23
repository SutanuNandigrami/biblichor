"""DOAB (Directory of Open Access Books) — ~90k OA scholarly books.
REST search at /rest/search; results carry DC metadata with download
URLs at oapen.relation.isPartOfBook or dc.identifier.uri."""
from __future__ import annotations

import logging

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.scrapers.http_client import make_client

log = logging.getLogger(__name__)

_API = "https://directory.doabooks.org/rest/search"


class Doab:
    name = "doab"
    provider = "doab"
    category = "pd"

    def __init__(self, cfg, **kw):
        self._cfg = cfg
        self.client = make_client(timeout=20)

    def search(self, query: SearchQuery) -> list[Candidate]:
        q = query.title or ""
        if query.language:
            q += f" AND language:{query.language}"
        try:
            r = self.client.get(_API, params={"expand": "metadata", "query": q})
        except Exception as e:
            log.warning("doab: request failed: %s", e)
            return []
        if r.status_code != 200:
            return []
        try:
            items = r.json() or []
        except Exception:
            return []
        out: list[Candidate] = []
        for it in items[:20]:
            # Keep first occurrence of each metadata key (DC convention: primary
            # value comes first; last-wins dict comprehension silently drops it).
            md: dict[str, str] = {}
            for kv in it.get("metadata", []):
                if kv["key"] not in md:
                    md[kv["key"]] = kv["value"]
            url = md.get("oapen.relation.isPartOfBook") or md.get("dc.identifier.uri")
            if not url:
                continue
            out.append(Candidate(
                provider=self.provider,
                md5=None,
                title=md.get("dc.title", query.title),
                author=md.get("dc.creator"),
                language=None,
                format="pdf",
                filesize_bytes=None,
                year=None,
                publisher=None,
                edition_hints="",
                detail_url=url,
            ))
        return out

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        return DownloadHandle(url=candidate.detail_url, headers={})
