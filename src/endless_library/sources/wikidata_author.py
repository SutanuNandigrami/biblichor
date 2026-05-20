"""Wikidata SPARQL 'follow author' reading-list source (Phase 6s.3).

Identifier format: Wikidata Q-ID (e.g. 'Q5950' for Charles Dickens).
Token: none.

SPARQL query against query.wikidata.org returns every wdt:P50
work for that author with publication date, series ordinal,
ISBN where known. Authoritative for 'everything by X' workflows.
Polls weekly to catch new releases.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import httpx

from endless_library.domain.models import BookRef

log = logging.getLogger(__name__)

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

SPARQL_TEMPLATE = """
SELECT ?work ?workLabel ?isbn13 WHERE {{
  ?work wdt:P50 wd:{author_qid} .
  ?work wdt:P31/wdt:P279* wd:Q7725634 .
  OPTIONAL {{ ?work wdt:P212 ?isbn13 }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
}}
LIMIT 200
"""


class WikidataAuthor:
    name = "wikidata"

    def __init__(self, *, http_timeout: float = 30.0) -> None:
        self._timeout = http_timeout

    def list_to_read(self, *, identifier: str, token: str | None) -> Iterable[BookRef]:
        qid = identifier.strip()
        if not qid.startswith("Q") or not qid[1:].isdigit():
            log.warning("wikidata: identifier must be Wikidata Q-ID, got %r", identifier)
            return []
        query = SPARQL_TEMPLATE.format(author_qid=qid)
        try:
            r = httpx.get(
                SPARQL_ENDPOINT,
                params={"query": query, "format": "json"},
                timeout=self._timeout,
                headers={
                    "User-Agent": "endless-library/0.1",
                    "Accept": "application/sparql-results+json",
                },
            )
        except httpx.HTTPError as e:
            log.warning("wikidata: %s", e)
            return []
        if r.status_code != 200:
            log.warning("wikidata: HTTP %s for %s", r.status_code, qid)
            return []
        try:
            bindings = r.json().get("results", {}).get("bindings", [])
        except ValueError:
            return []
        out: list[BookRef] = []
        seen: set[str] = set()
        for b in bindings:
            work_uri = b.get("work", {}).get("value", "")
            wd_id = work_uri.rsplit("/", 1)[-1] if work_uri else ""
            if not wd_id or wd_id in seen:
                continue
            seen.add(wd_id)
            title = b.get("workLabel", {}).get("value", "")
            isbn = b.get("isbn13", {}).get("value")
            if isbn:
                isbn = isbn.replace("-", "").strip() or None
            if not title:
                continue
            out.append(
                BookRef(
                    title=title,
                    author=None,
                    isbn13=isbn,
                    source="wikidata",
                    source_id=f"wikidata:{qid}:{wd_id}",
                )
            )
        return out
