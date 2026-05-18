from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class BookRef:
    """A book reference as returned by a Source adapter."""

    title: str
    author: str | None
    isbn13: str | None
    source: Literal["goodreads", "hardcover", "manual"]
    source_id: str
    source_added_at: str | None = None


@dataclass(frozen=True, slots=True)
class SearchQuery:
    title: str
    author: str | None
    isbn13: str | None
    format_priority: tuple[str, ...]
    language: str


@dataclass(frozen=True, slots=True)
class Candidate:
    """A search result before it's stored in the DB."""

    provider: Literal[
        "annas", "welib", "libgen", "archive", "kindlebangla", "ebanglalibrary", "zlib"
    ]
    md5: str | None
    title: str | None
    author: str | None
    language: str | None
    format: str | None
    filesize_bytes: int | None
    year: int | None
    publisher: str | None
    edition_hints: str
    detail_url: str
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DownloadHandle:
    url: str
    headers: dict[str, str]
    expected_filename: str | None = None
    expires_at: float | None = None


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    total: float
    components: dict[str, float]
    is_hard_skip: bool
    skip_reason: str | None = None
