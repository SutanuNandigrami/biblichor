from __future__ import annotations

import re
from typing import Protocol

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery

# Pattern from book-reader.user.js REMOTE_CONFIG (CDN d3/y URLs)
ANNAS_CDN_REGEX = re.compile(
    r'https://[^/\s"\']+/d3/y/[^/\s"\']+/[^/\s"\']+/[^~\s"\']+~[^/\s"\']+/[^"\'\s>]+',
    re.IGNORECASE,
)

BOOK_EXTENSIONS = {
    "epub",
    "mobi",
    "pdf",
    "djvu",
    "azw3",
    "azw",
    "fb2",
    "lit",
    "doc",
    "docx",
    "rtf",
    "txt",
    "cbz",
    "cbr",
}


class Scraper(Protocol):
    name: str
    provider: str

    def search(self, query: SearchQuery) -> list[Candidate]: ...

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None: ...


def url_has_book_ext(url: str) -> bool:
    path = url.lower().split("?", 1)[0].split("#", 1)[0]
    return any(path.endswith("." + ext) for ext in BOOK_EXTENSIONS)


def parse_filesize(text: str) -> int | None:
    """Parse strings like '2.3 MB', '450 KB', '1.2 GB' → bytes. None if unparseable."""
    if not text:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(KB|MB|GB|kB|mB|gB)\b", text, re.IGNORECASE)
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    unit = m.group(2).upper()
    mult = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}[unit]
    return int(val * mult)


class NotConfigured(Exception):
    """Raised when a scraper's required credentials / configuration are missing.

    Phase 6w.9b: moved here from scrapers.mobilism so bench.py can catch it
    without importing the mobilism module.
    """
