"""annas_parsing -- shared HTML parser for Anna's Archive search results.

Both ``annas_curl`` and ``annas_cloakbrowser`` hit the same Anna's Archive
search HTML (they differ only in transport).  This module contains the single
canonical parser so fixes apply once.

If you update extraction logic here, there is no other copy to update.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from endless_library.domain.models import Candidate
from endless_library.scrapers.base import parse_filesize


def parse_search_results(
    html: str,
    host_or_origin: str,
    *,
    fmt_hint: str = "epub",
    max_results: int = 25,
) -> list[Candidate]:
    """Parse an Anna's Archive /search HTML page into Candidates.

    Args:
        html:            Raw HTML from the search page.
        host_or_origin:  Either a bare hostname (``annas-archive.gl``) or a
                         full origin (``https://annas-archive.gl``).  Used to
                         build absolute ``detail_url`` values.
        fmt_hint:        Fallback format string when the row does not mention one.
        max_results:     Hard cap on returned candidates (default 25).
    """
    if not host_or_origin.startswith("http"):
        origin = f"https://{host_or_origin}"
    else:
        origin = host_or_origin.rstrip("/")

    soup = BeautifulSoup(html, "lxml")
    out: list[Candidate] = []
    seen: set[str] = set()

    # js-vim-focus is Anna's per-result title anchor; one per row, no sidebar dupes.
    for a in soup.select('a.js-vim-focus[href^="/md5/"]'):
        href = a.get("href", "")
        m = re.search(r"/md5/([a-f0-9]{32})", href)
        if not m:
            continue
        md5 = m.group(1)
        if md5 in seen:
            continue
        seen.add(md5)

        title = a.get_text(" ", strip=True) or None

        # Walk up to the row container (nearest ancestor div with "border-b")
        row = a
        for _ in range(6):
            row = row.parent
            if row is None:
                break
            if row.name == "div" and "border-b" in (row.get("class") or []):
                break
        row_text = row.get_text(" ", strip=True) if row else ""

        # Format: prefer the filename-hint sibling (contains ".<ext>"),
        # fall back to row_text scan.
        fmt = None
        for sib in (a.parent.children if a.parent else []):
            sib_text = (
                getattr(sib, "get_text", lambda **_: "")(" ", strip=True)
                if hasattr(sib, "get_text")
                else ""
            )
            fm = re.search(
                r"\.(epub|pdf|mobi|azw3|djvu|fb2|cbz|cbr|doc|docx|rtf|txt|lit)\b",
                sib_text,
                re.I,
            )
            if fm:
                fmt = fm.group(1).lower()
                break
        if not fmt:
            fmt = _parse_format(row_text) or fmt_hint

        filesize = parse_filesize(row_text)
        language = _parse_language(row_text)
        year = _parse_year(row_text)
        author = _parse_author(row_text)
        publisher = _parse_publisher(row_text)
        edition_hints = row_text.lower()[:400]
        isbns = extract_isbns(row_text)
        isbn13 = isbns[0] if isbns else None

        out.append(
            Candidate(
                provider="annas",
                md5=md5,
                title=title,
                author=author,
                language=language,
                format=fmt,
                filesize_bytes=filesize,
                year=year,
                publisher=publisher,
                edition_hints=edition_hints,
                detail_url=urljoin(origin, href),
                # isbn13 is stored in raw["isbns"] (list); scoring reads it from there.
                raw={"row_text": row_text[:400], "isbns": isbns, "isbn13": isbn13},
            )
        )
        if len(out) >= max_results:
            break
    return out


def extract_isbns(text: str) -> list[str]:
    """Pull every plausible ISBN-13 (and ISBN-10, normalised to 13) from *text*."""
    found: list[str] = []
    # ISBN-13: starts with 978 or 979, then 10 more digits. Allow hyphens.
    for m in re.finditer(r"\b(97[89][-\s]?(?:\d[-\s]?){9}\d)\b", text):
        digits = "".join(c for c in m.group(1) if c.isdigit())
        if len(digits) == 13:
            found.append(digits)
    # ISBN-10: 10 chars, last may be X. Convert to ISBN-13.
    for m in re.finditer(r"(?<!\d)((?:\d[-\s]?){9}[\dXx])(?!\d)", text):
        raw = m.group(1)
        cleaned = "".join(c for c in raw if c.isdigit() or c in "Xx")
        if len(cleaned) != 10:
            continue
        body = "978" + cleaned[:9]
        chk = (
            10 - sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(body)) % 10
        ) % 10
        found.append(body + str(chk))
    # Dedup, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for x in found:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _parse_format(s: str) -> str | None:
    m = re.search(r"\b(epub|pdf|mobi|azw3|djvu|fb2|cbz|cbr|doc|docx|rtf|txt|lit)\b", s, re.I)
    return m.group(1).lower() if m else None


def _parse_language(s: str) -> str | None:
    m = re.search(
        r"\b(English|German|Spanish|French|Italian|Russian|Portuguese|Chinese|Japanese|Hindi|Bengali)\b",
        s,
        re.I,
    )
    if not m:
        return None
    mapping = {
        "english": "en",
        "german": "de",
        "spanish": "es",
        "french": "fr",
        "italian": "it",
        "russian": "ru",
        "portuguese": "pt",
        "chinese": "zh",
        "japanese": "ja",
        "hindi": "hi",
        "bengali": "bn",
    }
    return mapping.get(m.group(1).lower())


def _parse_year(s: str) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", s)
    return int(m.group(0)) if m else None


def _parse_author(row_text: str) -> str | None:
    """Extract author from the slash-delimited filename hint in the row.

    Anna's rows include a filename hint like:
      nexusstc/Author Name/md5hash.epub
    The segment between source prefix and md5 is often the author or title.
    """
    m = re.search(
        r"(?:nexusstc|lgli|libgen|zlib|scihub|ia|fr|cerlalc)/([^/]+)/[a-f0-9]{32}\.",
        row_text,
        re.I,
    )
    if m:
        candidate_str = m.group(1).strip()
        if len(candidate_str) < 80 and not re.search(r"\d{4}", candidate_str):
            return candidate_str or None
    return None


def _parse_publisher(row_text: str) -> str | None:
    """Extract publisher from the metadata line, which looks like:
      English [en], epub, 2.3 MB, 2019, Publisher Name
    Publisher is the last comma-separated token after year.
    """
    m = re.search(r"\b(?:19|20)\d{2}\b[^,\n]*,\s*(.+?)(?:\s{2,}|\n|$)", row_text)
    if m:
        pub = m.group(1).strip().rstrip(".,;")
        if 2 < len(pub) < 80 and "/" not in pub:
            return pub
    return None
