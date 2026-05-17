"""Anna's Archive scraper using FlareSolverr to bypass Cloudflare challenges.

Reuses the parsing helpers from AnnasArchiveCurl so the only thing different is
how raw HTML is fetched — through FlareSolverr's headless Chromium instead of
curl-cffi."""

from __future__ import annotations

import logging
import random
import time

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.flaresolverr import FlareSolverr, FlareSolverrError
from endless_library.scrapers.annas_curl import AnnasArchiveCurl

log = logging.getLogger(__name__)


class AnnasArchiveFlareSolverr(AnnasArchiveCurl):
    name = "annas_flaresolverr"
    provider = "annas"

    def __init__(
        self,
        cfg: ScrapersCfg,
        *,
        flaresolverr: FlareSolverr | None = None,
    ) -> None:
        # Skip parent's http_get; we override _get entirely.
        super().__init__(cfg)
        self.fs = flaresolverr or FlareSolverr(cfg.flaresolverr_url)
        self._session_id: str | None = None

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        """Wrap the parent resolve_cdn in a single FS session so the countdown
        survives multiple polls."""
        # If a mock fs is in use (tests), MagicMock-style ducks work fine.
        try:
            self._session_id = self.fs.create_session()
        except FlareSolverrError as e:
            log.warning("annas_flaresolverr: could not create session: %s", e)
            self._session_id = None
        except Exception as e:  # noqa: BLE001
            # MagicMock returns a Mock object from create_session — treat as no session
            log.debug("annas_flaresolverr session create non-fatal: %s", e)
            self._session_id = None
        try:
            return super().resolve_cdn(candidate)
        finally:
            if self._session_id:
                try:
                    self.fs.destroy_session(self._session_id)
                except Exception:
                    pass
                self._session_id = None

    def _get(self, url: str) -> str | None:
        sleep = self.bucket.acquire(url)
        if sleep > 0:
            log.debug("rate-limit sleep %.1fs for %s", sleep, url)
            time.sleep(sleep)
        else:
            time.sleep(self.cfg.request_delay_seconds + random.uniform(0.5, 2.0))
        try:
            r = self.fs.get(url, session=self._session_id)
        except FlareSolverrError as e:
            log.warning("FlareSolverr error for %s: %s", url, e)
            return None
        except Exception as e:
            log.warning("FlareSolverr request failed for %s: %s", url, e)
            return None
        if r.status_code == 200:
            return r.text
        log.warning("FlareSolverr returned status %d for %s", r.status_code, url)
        if r.status_code in (403, 429, 503):
            self.mirrors.next_after_failure()
        return None

    def search(self, query: SearchQuery) -> list[Candidate]:
        return super().search(query)
