"""Z-Library SingleLogin personal-domain scraper (Phase 6s.5).

Mechanism (May 2026):
  1. POST credentials to singlelogin.re; response carries a
     per-user `personalDomain` like https://abc123.personal.z-library.bz.
  2. Subsequent searches go to /s/<query> on that personal domain.
  3. Domain TTL ~30 days; on 403/redirect-to-singlelogin we re-run
     the login flow.

Credentials live in the encrypted secrets store
(BookOrbitService.get/store_zlib_creds). No env-var path; SPA
saves them via the Scrapers page card.

Provider name 'zlib' deferred to Phase 6s.5 — for now we publish
as 'standard_ebooks' Literal not present, falling back to a
local-only candidate provider tag if used in tests.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.scrapers.http_client import BIBLICHOR_USER_AGENT

log = logging.getLogger(__name__)


class ZlibSingleLogin:
    """Z-Library scraper with SingleLogin personal-domain flow.

    cfg is the ScrapersCfg object. credentials read on-demand from
    the encrypted secrets store via a BookOrbitService instance
    constructed from cfg.general.books_dir + the recovery-key path.
    """

    name = "zlib_singlelogin"

    def __init__(self, cfg, db_path: Path | None = None) -> None:
        self._cfg = cfg
        self._db_path = db_path
        self._service = None  # lazy-built

    def _svc(self):
        if self._service is not None:
            return self._service
        # Lazy import to avoid circular
        from endless_library.bookorbit.service import BookOrbitService

        # We need a real cfg + db_path. If the caller didn't supply
        # db_path, this scraper can't operate (it has no creds source).
        if self._db_path is None:
            return None
        secrets_dir = Path(self._db_path).parent / "secrets"
        self._service = BookOrbitService(
            cfg=self._build_cfg_shim(),
            db_path=self._db_path,
            restore_key_path=secrets_dir / "restore.key",
        )
        return self._service

    def _build_cfg_shim(self):
        # BookOrbitService expects cfg.bookorbit.url etc; we don't
        # need bookorbit here but the service constructor wants it.
        # Synthesize a minimal stub.
        from types import SimpleNamespace

        return SimpleNamespace(
            bookorbit=SimpleNamespace(enabled=False, url="", library_root="", library_id=""),
            general=SimpleNamespace(books_dir=str(self._db_path.parent / "books"))
            if self._db_path
            else SimpleNamespace(books_dir="."),
        )

    def _ensure_personal_domain(self) -> str | None:
        svc = self._svc()
        if svc is None:
            return None
        cached_domain = svc.get_secret_value("zlib.personal_domain")
        cached_expires = svc.get_secret_value("zlib.domain_expires_at")
        if cached_domain and cached_expires:
            try:
                if int(cached_expires) > int(time.time()):
                    return cached_domain
            except ValueError:
                pass
        # (Re-)login
        creds = svc.get_zlib_creds()
        if creds is None:
            log.info("zlib_singlelogin: no credentials stored; skipping")
            return None
        email, password = creds
        try:
            r = httpx.post(
                "https://singlelogin.re/rpc.php",
                data={
                    "isModal": "true",
                    "email": email,
                    "password": password,
                    "site_mode": "books",
                    "action": "login",
                    "redirectUrl": "",
                    "gg_json_mode": "1",
                },
                timeout=30.0,
                headers={"User-Agent": BIBLICHOR_USER_AGENT},
                follow_redirects=True,
            )
        except httpx.HTTPError as e:
            log.warning("zlib login: %s", e)
            return None
        if r.status_code != 200:
            log.warning("zlib login: HTTP %s", r.status_code)
            return None
        try:
            data = r.json()
        except ValueError:
            return None
        # The response shape carries either response.personalDomain or
        # response.validationError + a redirect target. Tolerate both.
        domain = (data.get("response") or {}).get("personalDomain") or data.get("personalDomain")
        if not domain:
            log.warning("zlib login: response missing personalDomain")
            return None
        domain = domain.rstrip("/")
        # Cache for ~30 days
        svc.set_secret_value("zlib.personal_domain", domain)
        svc.set_secret_value("zlib.domain_expires_at", str(int(time.time()) + 30 * 24 * 3600))
        return domain

    def search(self, sq: SearchQuery) -> list[Candidate]:
        if not sq.title:
            return []
        domain = self._ensure_personal_domain()
        if not domain:
            return []
        q = sq.title if not sq.author else f"{sq.title} {sq.author}"
        try:
            r = httpx.get(
                f"{domain}/s/{quote(q)}",
                timeout=20.0,
                headers={"User-Agent": BIBLICHOR_USER_AGENT},
                follow_redirects=True,
            )
        except httpx.HTTPError as e:
            log.info("zlib search: %s", e)
            return []
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "lxml")
        out: list[Candidate] = []
        for card in soup.select("div.book-card, .book-item, z-bookcard")[:25]:
            title_el = card.select_one(".bookTitle, h3 a, [slot='title']")
            title = title_el.get_text(" ", strip=True) if title_el else ""
            if not title:
                continue
            author_el = card.select_one(".authors, [slot='author']")
            author = author_el.get_text(" ", strip=True) if author_el else None
            href = ""
            link_el = card.select_one("a")
            if link_el:
                href = link_el.get("href", "")
            fmt_el = card.select_one(".property_format, [slot='format']")
            fmt = fmt_el.get_text(strip=True).lower() if fmt_el else "epub"
            if fmt in ("epub", "pdf", "mobi", "azw3"):
                pass
            else:
                fmt = "epub"
            out.append(
                Candidate(
                    provider="zlib",
                    md5=None,
                    title=title,
                    author=author,
                    language="en",
                    format=fmt,
                    filesize_bytes=None,
                    year=None,
                    publisher=None,
                    edition_hints="zlib-singlelogin",
                    detail_url=urljoin(domain, href) if href else domain,
                )
            )
        return out

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        # Following the per-book download chain is left for future
        # iteration. For now, the search candidates are usable directly
        # only if the detail_url already points at the file.
        if not candidate.detail_url:
            return None
        if candidate.detail_url.endswith((".epub", ".pdf", ".mobi", ".azw3")):
            return DownloadHandle(url=candidate.detail_url, headers={})
        return None
