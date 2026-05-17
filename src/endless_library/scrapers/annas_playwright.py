from __future__ import annotations

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery


class AnnasArchivePlaywright:
    name = "annas_playwright"
    provider = "annas"

    def __init__(self, cfg: ScrapersCfg) -> None:
        self.cfg = cfg

    def search(self, query: SearchQuery) -> list[Candidate]:
        raise NotImplementedError("Playwright strategy lands in Phase 5b")

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        raise NotImplementedError("Playwright strategy lands in Phase 5b")
