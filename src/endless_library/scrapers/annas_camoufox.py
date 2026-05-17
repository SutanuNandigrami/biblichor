from __future__ import annotations

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery


class AnnasArchiveCamoufox:
    name = "annas_camoufox"
    provider = "annas"

    def __init__(self, cfg: ScrapersCfg) -> None:
        self.cfg = cfg

    def search(self, query: SearchQuery) -> list[Candidate]:
        raise NotImplementedError("Camoufox strategy lands in Phase 5b")

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        raise NotImplementedError("Camoufox strategy lands in Phase 5b")
