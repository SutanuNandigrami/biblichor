"""kindlebangla.com — Bengali ebook source backed by Google Drive.

Search: GET /index.php?search=<urlencoded-bengali>
        → HTML grid of cards, each with:
            - <a href="/book/<slug>">  : link to detail page (slug is Bengali)
            - <img alt="<title>" src="<cloudinary-cover-url>">
            - <h3>title</h3>
            - <p>author</p>
            - <span>category</span>

resolve_cdn:
  1. We already have the book slug from the search Candidate.
     Optionally fetch /book/<slug> to confirm the download link still exists
     and read the og:image. We skip this for speed and trust the cards.
  2. HEAD /download/<slug> → 302 to either:
       - https://drive.google.com/file/d/<FILE_ID>/view  (~70% of items)
       - https://drive.google.com/drive/folders/<FOLDER_ID>  (~30%)
  3. For files: hand to drive_helpers.resolve_download_url(file_id)
     For folders: list contents via embeddedfolderview, find .epub matching
     title, then resolve_download_url() on that file_id.

Native format: EPUB. No Calibre conversion needed.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from endless_library.config import KindleBanglaCfg
from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.scrapers.drive_helpers import (
    find_in_folder,
    list_folder,
    parse_drive_url,
    resolve_download_url,
)
from endless_library.scrapers.http_client import BIBLICHOR_USER_AGENT as _BIBLICHOR_UA
from endless_library.scrapers.http_client import make_client

log = logging.getLogger(__name__)

BASE = "https://www.kindlebangla.com"
USER_AGENT = f"Mozilla/5.0 (compatible; {_BIBLICHOR_UA}; +kindlebangla scraper)"


class KindleBanglaCurl:
    """Strategy entry: kindlebangla_curl."""

    name = "kindlebangla_curl"
    provider = "kindlebangla"

    def __init__(
        self,
        cfg: KindleBanglaCfg,
        *,
        http_get: Any | None = None,
        # http_redirect: callable(url) -> (status, headers, body) — used by
        # resolve_cdn to follow the /download/<slug> 302 without httpx.
        http_redirect: Any | None = None,
    ) -> None:
        self.cfg = cfg
        self._http_get = http_get
        self._http_redirect = http_redirect

    # ---------------- Public API ----------------

    def search(self, query: SearchQuery) -> list[Candidate]:
        results = self._search_upstream(query)
        excluded = set(getattr(self.cfg, "excluded_categories", None) or [])
        if not excluded:
            return results
        filtered = []
        for c in results:
            # categories tuple from the card; also check edition_hints for compat
            cats = set(c.categories) | ({c.edition_hints} if c.edition_hints else set())
            if cats & excluded:
                continue
            filtered.append(c)
        return filtered

    def _search_upstream(self, query: SearchQuery) -> list[Candidate]:
        url = f"{BASE}/index.php?search={quote_plus(query.title)}"
        html = self._get_text(url)
        if not html:
            return []
        return self._parse_search(html)

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        slug = (candidate.raw or {}).get("slug")
        if not slug:
            log.warning("kindlebangla: candidate has no slug")
            return None

        # Follow /download/<slug> 302 to its Drive target.
        download_url = f"{BASE}/download/{quote_plus(slug)}"
        redirect_location = self._get_redirect_location(download_url)
        if not redirect_location:
            log.warning("kindlebangla: no redirect from %s", download_url)
            return None

        target = parse_drive_url(redirect_location)
        if not target:
            log.warning("kindlebangla: unexpected target %s", redirect_location)
            return None

        if target.kind == "file":
            return self._handle_for_file(target.drive_id, candidate)

        # Folder: list, pick the EPUB matching the title
        entries = list_folder(target.drive_id)
        if not entries:
            log.warning("kindlebangla: empty folder %s", target.drive_id)
            return None
        match = find_in_folder(entries, candidate.title or "", ext=".epub")
        if match is None:
            # Try PDF as a fallback
            match = find_in_folder(entries, candidate.title or "", ext=".pdf")
        if match is None:
            log.warning("kindlebangla: no matching file in folder %s", target.drive_id)
            return None
        return self._handle_for_file(match.drive_id, candidate, filename=match.filename)

    # ---------------- Internals ----------------

    def _parse_search(self, html: str) -> list[Candidate]:
        soup = BeautifulSoup(html, "lxml")
        # Each card is a div containing the cover image + h3 title + p author.
        # We anchor on the /book/<slug> link.
        cards: list[Candidate] = []
        seen_slugs: set[str] = set()
        for a in soup.select('a[href^="/book/"]'):
            slug = a.get("href", "/book/").split("/book/", 1)[1].split("?")[0]
            if not slug or slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            # Walk up to find the card container
            card = a.parent
            for _ in range(4):
                if card is None:
                    break
                if card.find("h3") is not None:
                    break
                card = card.parent
            if card is None:
                continue

            title_el = card.find("h3")
            author_el = card.find("p")
            img = card.find("img")
            category_el = card.find("span")

            title = title_el.get_text(" ", strip=True) if title_el else None
            author = author_el.get_text(" ", strip=True) if author_el else None
            cover = img.get("src") if img else None
            category = category_el.get_text(" ", strip=True) if category_el else None

            if not title:
                continue

            cards.append(
                Candidate(
                    provider="kindlebangla",
                    md5=None,
                    title=title,
                    author=author,
                    language="bn",
                    format="epub",  # site is EPUB-first; folder fallback handles PDF
                    filesize_bytes=None,
                    year=None,
                    publisher=None,
                    edition_hints=category or "",
                    detail_url=urljoin(BASE, a.get("href", "")),
                    categories=(category,) if category else (),
                    raw={"slug": slug, "cover_url": cover},
                )
            )
        return cards

    def _handle_for_file(
        self, drive_id: str, candidate: Candidate, *, filename: str | None = None
    ) -> DownloadHandle | None:
        resolved = resolve_download_url(drive_id)
        if not resolved:
            return None
        url, header_overrides = resolved
        headers = {"User-Agent": USER_AGENT, **header_overrides}
        # We don't always know the filename ahead of the download; derive one
        # from the title if the folder listing didn't provide one.
        if not filename:
            filename = (candidate.title or drive_id).replace("/", "_") + ".epub"
        return DownloadHandle(
            url=url,
            headers=headers,
            expected_filename=filename,
        )

    def _get_text(self, url: str) -> str | None:
        if self._http_get is not None:
            status, body = self._http_get(url)
            if status != 200:
                return None
            return body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
        try:
            r = make_client(timeout=20.0).get(
                url,
                allow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
        except Exception as e:
            log.warning("kindlebangla: GET %s failed: %s", url, e)
            return None
        if r.status_code != 200:
            return None
        return r.text

    def _get_redirect_location(self, url: str) -> str | None:
        if self._http_redirect is not None:
            status, headers, _body = self._http_redirect(url)
            if status in (301, 302, 303):
                return headers.get("Location") or headers.get("location")
            return None
        try:
            r = make_client(timeout=15.0).get(
                url,
                allow_redirects=False,
                headers={"User-Agent": USER_AGENT},
            )
        except Exception as e:
            log.warning("kindlebangla: HEAD %s failed: %s", url, e)
            return None
        if r.status_code not in (301, 302, 303):
            return None
        return r.headers.get("Location")
