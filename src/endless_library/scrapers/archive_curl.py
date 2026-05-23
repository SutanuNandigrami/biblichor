"""Internet Archive (archive.org) scraper.

archive.org is the only "official, public-API" source we integrate. There's no
anti-bot to fight, no mirror rotation needed, and the JSON contracts have been
stable for years. We use three endpoints:

  search    GET archive.org/advancedsearch.php?q=...&fl[]=...&rows=N&output=json
  files     GET archive.org/metadata/<identifier>/files
  download  GET archive.org/download/<identifier>/<filename>      (HTTP 302 to a CDN)

Items at IA fall into two tiers:
  - public-domain / opensource — files are downloadable directly
  - borrow-only (collection: inlibrary) — files have `"private": "true"`; you
    can only stream-borrow them in the browser. We filter those out at query
    construction time (`-collection:inlibrary`) and again at file selection
    time (skip private=true).

Coverage: very strong for English public-domain classics, scanned old print
runs of magazines/journals, academic texts (computer science especially),
and the Project Gutenberg ingest. Weak for contemporary copyrighted titles.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus

from endless_library.scrapers.http_client import make_client

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery

log = logging.getLogger(__name__)

BASE = "https://archive.org"
USER_AGENT = "biblichor/0.1 (+archive.org scraper)"

# Map IA's `format` strings to our short codes. The same item often lists many
# formats — we want only ones we can hand to Kindle (after optional Calibre
# conversion).
_IA_FORMAT_MAP = {
    "epub": "epub",
    "lcp encrypted epub": None,  # DRM, skip
    "text pdf": "pdf",
    "image pdf": "pdf",
    "additional text pdf": "pdf",
    "abbyy pdf": "pdf",
    "lcp encrypted pdf": None,  # DRM, skip
    "mobi": "mobi",
    "kindle": "mobi",
    "azw3": "azw3",
}


def _ia_format(raw: str | None) -> str | None:
    if not raw:
        return None
    return _IA_FORMAT_MAP.get(raw.strip().lower())


class ArchiveOrgCurl:
    """Strategy entry: archive_org_curl."""

    name = "archive_curl"
    provider = "archive"

    def __init__(
        self,
        cfg: ScrapersCfg,
        *,
        http_get: Any | None = None,  # callable(url) -> (status, body) for tests
    ) -> None:
        self.cfg = cfg
        self._http_get = http_get

    # ------------------------------------------------------------------ Public API

    def search(self, query: SearchQuery) -> list[Candidate]:
        q_terms = []
        # Quote phrases so multi-word titles match together
        q_terms.append(f'title:"{self._escape(query.title)}"')
        if query.author:
            q_terms.append(f'creator:"{self._escape(query.author)}"')
        q_terms.append("mediatype:texts")
        # Exclude borrow-only items (they have private=true files we can't fetch)
        q_terms.append("-collection:inlibrary")
        # Bias to formats we can use directly. IA `format` faceting expands
        # automatically, so listing just EPUB still surfaces items that also
        # have PDF available.
        q_terms.append('(format:"EPUB" OR format:"Text PDF" OR format:"MOBI")')

        url = (
            f"{BASE}/advancedsearch.php"
            f"?q={quote_plus(' AND '.join(q_terms))}"
            f"&fl[]=identifier&fl[]=title&fl[]=creator&fl[]=year&fl[]=format"
            f"&fl[]=publisher&fl[]=language&fl[]=collection"
            f"&rows=20&output=json"
        )
        data = self._get_json(url)
        if not data:
            return []
        candidates: list[Candidate] = []
        for doc in data.get("response", {}).get("docs", []):
            cand = self._candidate_from_doc(doc, query)
            if cand is not None:
                candidates.append(cand)
        return candidates

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        """Look up the per-item file list, pick the best matching format,
        return a DownloadHandle whose URL is `archive.org/download/<id>/<name>`.

        The Pipeline's download() helper follows the 302 to the CDN and
        streams normally.
        """
        identifier = (candidate.raw or {}).get("identifier")
        if not identifier:
            log.warning("archive: no identifier on candidate %s", candidate.detail_url)
            return None
        files_url = f"{BASE}/metadata/{identifier}/files"
        data = self._get_json(files_url)
        if not data:
            return None
        files = data.get("result") or []
        chosen = self._pick_file(files, candidate.format)
        if not chosen:
            log.info("archive: no usable file in %s", identifier)
            return None
        url = f"{BASE}/download/{identifier}/{quote_plus(chosen['name'], safe='.-_')}"
        return DownloadHandle(
            url=url,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
            expected_filename=chosen["name"],
        )

    # ------------------------------------------------------------------ Internals

    @staticmethod
    def _escape(s: str) -> str:
        # Strip characters that would break the Lucene query
        return s.replace('"', " ").replace("\\", " ").strip()

    def _get_json(self, url: str) -> dict | None:
        try:
            if self._http_get is not None:
                status, body = self._http_get(url)
            else:
                r = make_client(timeout=20.0).get(
                    url,
                    allow_redirects=True,
                    headers={"User-Agent": USER_AGENT},
                )
                status, body = r.status_code, r.content
        except Exception as e:
            log.warning("archive: fetch %s failed: %s", url, e)
            return None
        if status != 200:
            log.warning("archive: %s returned %s", url, status)
            return None
        try:
            import json

            return json.loads(body)
        except Exception as e:
            log.warning("archive: bad JSON from %s: %s", url, e)
            return None

    def _candidate_from_doc(self, doc: dict, q: SearchQuery) -> Candidate | None:
        identifier = doc.get("identifier")
        if not identifier:
            return None
        creator = doc.get("creator")
        if isinstance(creator, list):
            creator = creator[0] if creator else None

        # Pick the best format from doc.format (a list)
        raw_formats = doc.get("format") or []
        if isinstance(raw_formats, str):
            raw_formats = [raw_formats]
        mapped = [_ia_format(f) for f in raw_formats]
        mapped = [m for m in mapped if m]
        # Prefer the user's preference order
        chosen_fmt: str | None = None
        for pref in q.format_priority:
            if pref in mapped:
                chosen_fmt = pref
                break
        if chosen_fmt is None:
            chosen_fmt = mapped[0] if mapped else None

        lang = doc.get("language")
        if isinstance(lang, list):
            lang = lang[0] if lang else None
        if isinstance(lang, str):
            lang = lang.lower()[:2]  # "English" -> "en", "Bengali" -> "be" (close enough)

        return Candidate(
            provider="archive",
            md5=None,  # IA doesn't expose a single MD5 at the doc level
            title=doc.get("title"),
            author=creator,
            language=lang,
            format=chosen_fmt,
            filesize_bytes=None,  # populated during resolve_cdn
            year=self._maybe_int(doc.get("year")),
            publisher=doc.get("publisher") if isinstance(doc.get("publisher"), str) else None,
            edition_hints="",
            detail_url=f"{BASE}/details/{identifier}",
            raw={"identifier": identifier, "collection": doc.get("collection")},
        )

    @staticmethod
    def _maybe_int(v) -> int | None:
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _pick_file(self, files: list[dict], preferred_format: str | None) -> dict | None:
        """Pick the best downloadable file from an IA item.

        Order: requested format > epub > azw3 > mobi > pdf. Skip anything
        marked private (borrow-only) or LCP-encrypted.
        """
        order = ["epub", "azw3", "mobi", "pdf"]
        if preferred_format and preferred_format in order:
            order = [preferred_format] + [f for f in order if f != preferred_format]

        for want in order:
            for f in files:
                if str(f.get("private", "")).lower() == "true":
                    continue
                fmt = (f.get("format") or "").lower()
                if "lcp encrypted" in fmt:
                    continue
                name = (f.get("name") or "").lower()
                if want == "epub" and name.endswith(".epub") and "lcp" not in name:
                    return f
                if want == "azw3" and name.endswith(".azw3"):
                    return f
                if want == "mobi" and name.endswith(".mobi"):
                    return f
                if want == "pdf" and name.endswith(".pdf") and "lcp" not in name:
                    return f
        return None
