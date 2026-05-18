"""Tests for kindlebangla_curl + drive_helpers."""

from __future__ import annotations

from endless_library.config import ScrapersCfg
from endless_library.domain.models import SearchQuery
from endless_library.scrapers.drive_helpers import (
    DriveFolderEntry,
    find_in_folder,
    parse_drive_url,
)
from endless_library.scrapers.kindlebangla_curl import KindleBanglaCurl

# ============ drive_helpers ============


def test_parse_drive_url_file():
    t = parse_drive_url(
        "https://drive.google.com/file/d/1NW4-EIDqKY4y0du1QMlE-ln47TPb4I9X/view?usp=sharing"
    )
    assert t is not None
    assert t.kind == "file"
    assert t.drive_id == "1NW4-EIDqKY4y0du1QMlE-ln47TPb4I9X"


def test_parse_drive_url_folder():
    t = parse_drive_url(
        "https://drive.google.com/drive/folders/1_FWBiEYtmquHvvjr77zUOEtfbeIvmuff?usp=sharing"
    )
    assert t is not None
    assert t.kind == "folder"
    assert t.drive_id == "1_FWBiEYtmquHvvjr77zUOEtfbeIvmuff"


def test_parse_drive_url_uc_link():
    t = parse_drive_url(
        "https://drive.google.com/uc?export=download&id=1xUZWVmhw7homu9t2_txA4LiooXAW4Zc-"
    )
    assert t is not None
    assert t.kind == "file"
    assert t.drive_id == "1xUZWVmhw7homu9t2_txA4LiooXAW4Zc-"


def test_parse_drive_url_returns_none_for_garbage():
    assert parse_drive_url("https://example.com/not-drive") is None
    assert parse_drive_url("") is None
    assert parse_drive_url(None) is None  # type: ignore[arg-type]


def test_find_in_folder_picks_epub_when_one_match():
    entries = [
        DriveFolderEntry(filename="cover.jpg", drive_id="A"),
        DriveFolderEntry(filename="book.epub", drive_id="B"),
        DriveFolderEntry(filename="meta.opf", drive_id="C"),
    ]
    m = find_in_folder(entries, title="anything")
    assert m is not None and m.drive_id == "B"


def test_find_in_folder_returns_none_when_no_epub():
    entries = [
        DriveFolderEntry(filename="x.kfx", drive_id="A"),
        DriveFolderEntry(filename="cover.jpg", drive_id="B"),
    ]
    assert find_in_folder(entries, "anything") is None


def test_find_in_folder_falls_back_to_pdf_when_ext_changed():
    entries = [
        DriveFolderEntry(filename="book.pdf", drive_id="A"),
        DriveFolderEntry(filename="cover.jpg", drive_id="B"),
    ]
    m = find_in_folder(entries, "anything", ext=".pdf")
    assert m is not None and m.drive_id == "A"


def test_find_in_folder_disambiguates_by_title_overlap():
    """When multiple .epub files exist, pick the one whose name shares
    the most tokens with the target title."""
    entries = [
        DriveFolderEntry(filename="হিমু সমগ্র-৫ - হুমায়ূন আহমেদ.epub", drive_id="WRONG"),
        DriveFolderEntry(filename="হিমু সমগ্র-১ - হুমায়ূন আহমেদ.epub", drive_id="RIGHT"),
    ]
    m = find_in_folder(entries, "হিমু সমগ্র-১")
    assert m is not None and m.drive_id == "RIGHT"


# ============ kindlebangla_curl: parser ============


SEARCH_HTML = """
<html><body>
<div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
  <div class="bg-white rounded-xl shadow-lg overflow-hidden">
    <div class="relative overflow-hidden h-64">
      <img alt="হিমু সমগ্র-১" src="https://cdn.example.com/cover-1.webp" />
      <div class="absolute inset-0 bg-black bg-opacity-40 flex items-center justify-center">
        <a class="bg-primary" href="/book/হিমু-সমগ্র-১">বিস্তারিত</a>
      </div>
    </div>
    <div class="p-4">
      <h3 class="text-xl font-bold mb-1 truncate">হিমু সমগ্র-১</h3>
      <p class="text-gray-500 text-sm mb-2">হুমায়ূন আহমেদ</p>
      <div class="flex justify-between"><span class="bg-gray-200">হিমু সিরিজ</span></div>
    </div>
  </div>

  <div class="bg-white rounded-xl shadow-lg overflow-hidden">
    <div class="relative overflow-hidden h-64">
      <img alt="হিমু সমগ্র-২" src="https://cdn.example.com/cover-2.webp" />
      <div class="absolute inset-0 bg-black bg-opacity-40 flex items-center justify-center">
        <a class="bg-primary" href="/book/হিমু-সমগ্র-২">বিস্তারিত</a>
      </div>
    </div>
    <div class="p-4">
      <h3 class="text-xl font-bold mb-1 truncate">হিমু সমগ্র-২</h3>
      <p class="text-gray-500 text-sm mb-2">হুমায়ূন আহমেদ</p>
      <div class="flex justify-between"><span class="bg-gray-200">হিমু সিরিজ</span></div>
    </div>
  </div>
</div>
</body></html>
"""


def _kb_with_search_html(html: str) -> KindleBanglaCurl:
    """Build a scraper that returns `html` from any GET."""
    s = KindleBanglaCurl(
        ScrapersCfg(),
        http_get=lambda _url: (200, html.encode("utf-8")),
    )
    return s


def test_search_parses_cards():
    s = _kb_with_search_html(SEARCH_HTML)
    q = SearchQuery(
        title="হিমু",
        author=None,
        isbn13=None,
        format_priority=("epub",),
        language="bn",
    )
    hits = s.search(q)
    assert len(hits) == 2
    assert hits[0].provider == "kindlebangla"
    assert hits[0].title == "হিমু সমগ্র-১"
    assert hits[0].author == "হুমায়ূন আহমেদ"
    assert hits[0].language == "bn"
    assert hits[0].format == "epub"
    assert hits[0].edition_hints == "হিমু সিরিজ"
    assert hits[0].raw["slug"] == "হিমু-সমগ্র-১"
    assert hits[0].raw["cover_url"].startswith("https://cdn.example.com/")


def test_search_dedupes_repeat_book_links():
    """If the same /book/<slug> appears twice (some sites do that on the
    cover image + the title), we should only return one card."""
    dup = (
        SEARCH_HTML.replace(
            'class="bg-primary"',
            'class="bg-primary first"',
        )
        .replace(
            '<img alt="হিমু সমগ্র-১"',
            '<a href="/book/হিমু-সমগ্র-১"><img alt="হিমু সমগ্র-১"',
        )
        .replace(
            'src="https://cdn.example.com/cover-1.webp" />',
            'src="https://cdn.example.com/cover-1.webp" /></a>',
            1,
        )
    )
    s = _kb_with_search_html(dup)
    q = SearchQuery(
        title="হিমু",
        author=None,
        isbn13=None,
        format_priority=("epub",),
        language="bn",
    )
    hits = s.search(q)
    slugs = [h.raw["slug"] for h in hits]
    assert len(slugs) == len(set(slugs)), f"duplicate slugs: {slugs}"


def test_search_empty_returns_empty():
    s = KindleBanglaCurl(ScrapersCfg(), http_get=lambda _u: (404, b""))
    q = SearchQuery(
        title="x",
        author=None,
        isbn13=None,
        format_priority=("epub",),
        language="bn",
    )
    assert s.search(q) == []


def test_search_url_quotes_bengali():
    """Make sure the search URL properly URL-encodes Bengali characters."""
    captured: list[str] = []

    def cap(url):
        captured.append(url)
        return 200, b"<html></html>"

    s = KindleBanglaCurl(ScrapersCfg(), http_get=cap)
    q = SearchQuery(
        title="হিমু",
        author=None,
        isbn13=None,
        format_priority=("epub",),
        language="bn",
    )
    s.search(q)
    assert captured
    # The %-encoded Bengali for হিমু should be in the URL
    assert "%E0%A6%B9%E0%A6%BF%E0%A6%AE%E0%A7%81" in captured[0]


# ============ kindlebangla_curl: resolve_cdn (mocked) ============


def test_resolve_cdn_returns_none_when_no_slug():
    from endless_library.domain.models import Candidate

    s = KindleBanglaCurl(ScrapersCfg())
    cand = Candidate(
        provider="kindlebangla",
        md5=None,
        title="X",
        author=None,
        language="bn",
        format="epub",
        filesize_bytes=None,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url="",
        raw={},  # no slug
    )
    assert s.resolve_cdn(cand) is None
