"""HathiTrust PD lookup. Only fires when SearchQuery carries an ISBN13
and matched records have a public-domain rights code. Full-text
search via Hathifiles bulk ingestion is deferred (see spec risks)."""

from __future__ import annotations

import logging

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.scrapers.http_client import make_client

log = logging.getLogger(__name__)

_PD_RIGHTS = {"pd", "pdus"}


class HathiTrust:
    name = "hathitrust"
    provider = "hathitrust"
    category = "pd"

    def __init__(self, cfg, **kw):
        self._cfg = cfg

    def search(self, query: SearchQuery) -> list[Candidate]:
        if not query.isbn13:
            return []
        url = f"https://catalog.hathitrust.org/api/volumes/brief/json/isbn:{query.isbn13}"
        client = make_client(timeout=20)
        try:
            r = client.get(url)
        except Exception as e:
            log.warning("hathitrust: lookup failed: %s", e)
            return []
        finally:
            if hasattr(client, "close"):
                client.close()
        if r.status_code != 200:
            return []
        try:
            body = r.json()
        except Exception:
            return []
        out: list[Candidate] = []
        for rec in body.get("records", {}).values():
            title = (rec.get("titles") or [query.title])[0]
            for item in rec.get("items", []):
                if item.get("rightsCode") in _PD_RIGHTS:
                    htid = item.get("htid")
                    if not htid:
                        continue
                    dl = f"https://babel.hathitrust.org/cgi/imgsrv/download/pdf?id={htid}"
                    out.append(
                        Candidate(
                            provider=self.provider,
                            md5=None,
                            title=title,
                            author=None,
                            language=None,
                            format="pdf",
                            filesize_bytes=None,
                            year=None,
                            publisher=None,
                            edition_hints="",
                            detail_url=dl,
                        )
                    )
        return out

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        return DownloadHandle(url=candidate.detail_url, headers={})
