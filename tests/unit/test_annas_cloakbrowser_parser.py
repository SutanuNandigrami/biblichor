"""Cloakbrowser parser must produce same-shape candidates as annas_curl
since they parse identical Anna's Archive HTML.

The shared parser lives in annas_parsing.py; this file verifies that
_parse_search_results in annas_cloakbrowser delegates correctly.
"""

from __future__ import annotations

from pathlib import Path

from endless_library.scrapers.annas_cloakbrowser import _parse_search_results

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "annas" / "search_pragmatic.html"

# Fixture HTML extended with an ISBN-bearing row for testing isbn13 extraction.
ISBN_HTML = """\
<!doctype html><html><body>
<main>
  <div class="js-aarecord-list-outer">
    <div class="flex pt-3 pb-3 border-b border-gray-100">
      <div class="max-w-full overflow-hidden flex flex-col">
        <div class="line-clamp-[2] overflow-hidden break-words text-[9px] text-gray-500 font-mono">
          nexusstc/David Thomas, Andrew Hunt/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.epub
        </div>
        <a class="line-clamp-[3] overflow-hidden break-words js-vim-focus custom-a"
           href="/md5/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">
          The Pragmatic Programmer: 20th Anniversary Edition
        </a>
        <div class="text-xs">English [en], epub, 2.3 MB, 2019, Pragmatic Bookshelf</div>
        <div class="text-xs text-gray-500">ISBN: 9780135957059</div>
      </div>
    </div>
    <div class="flex pt-3 pb-3 border-b border-gray-100">
      <div class="max-w-full overflow-hidden flex flex-col">
        <div class="line-clamp-[2] overflow-hidden break-words text-[9px] text-gray-500 font-mono">
          nexusstc/Andrew Hunt/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.pdf
        </div>
        <a class="js-vim-focus custom-a" href="/md5/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb">
          The Pragmatic Programmer (1st edition)
        </a>
        <div class="text-xs">English [en], pdf, 12.5 MB, 1999, Addison-Wesley</div>
        <div class="text-xs text-gray-500">ISBN: 9780201616224</div>
      </div>
    </div>
  </div>
</main>
</body></html>"""


class TestCloakbrowserParserWithFixture:
    """Tests against the standard search_pragmatic.html fixture."""

    def test_extracts_correct_count(self):
        html = FIXTURE.read_text()
        candidates = _parse_search_results(html, "annas-archive.gl")
        # Fixture has 3 js-vim-focus rows; sidebar anchors must be excluded.
        assert len(candidates) == 3

    def test_first_result_md5_and_title(self):
        html = FIXTURE.read_text()
        candidates = _parse_search_results(html, "annas-archive.gl")
        first = candidates[0]
        assert first.md5 == "a" * 32
        assert "Pragmatic" in (first.title or "")

    def test_detail_url_is_absolute(self):
        html = FIXTURE.read_text()
        candidates = _parse_search_results(html, "annas-archive.gl")
        for c in candidates:
            assert c.detail_url.startswith("https://"), f"relative url: {c.detail_url}"

    def test_format_extracted_from_metadata_line(self):
        html = FIXTURE.read_text()
        candidates = _parse_search_results(html, "annas-archive.gl")
        fmts = [c.format for c in candidates]
        assert "epub" in fmts
        assert "pdf" in fmts

    def test_year_extracted(self):
        html = FIXTURE.read_text()
        candidates = _parse_search_results(html, "annas-archive.gl")
        years = [c.year for c in candidates if c.year]
        assert any(1700 < y < 2030 for y in years), f"no plausible years: {years}"
        assert candidates[0].year == 2019

    def test_filesize_bytes_extracted(self):
        html = FIXTURE.read_text()
        candidates = _parse_search_results(html, "annas-archive.gl")
        sizes = [c.filesize_bytes for c in candidates if c.filesize_bytes]
        assert sizes, "no filesizes extracted"
        assert all(s > 0 for s in sizes)
        # "2.3 MB" -> ~2.4M bytes
        assert candidates[0].filesize_bytes > 1_000_000

    def test_language_extracted(self):
        html = FIXTURE.read_text()
        candidates = _parse_search_results(html, "annas-archive.gl")
        langs = [c.language for c in candidates if c.language]
        assert langs, "no languages extracted"
        assert "en" in langs

    def test_sidebar_anchors_excluded(self):
        html = FIXTURE.read_text()
        candidates = _parse_search_results(html, "annas-archive.gl")
        md5s = {c.md5 for c in candidates}
        assert "d" * 32 not in md5s
        assert "e" * 32 not in md5s

    def test_accepts_full_origin_url(self):
        html = FIXTURE.read_text()
        candidates = _parse_search_results(html, "https://annas-archive.gl")
        assert all(c.detail_url.startswith("https://annas-archive.gl") for c in candidates)

    def test_publisher_extracted(self):
        html = FIXTURE.read_text()
        candidates = _parse_search_results(html, "annas-archive.gl")
        # First row has "Pragmatic Bookshelf" in metadata line
        publishers = [c.publisher for c in candidates if c.publisher]
        assert publishers, "no publishers extracted"


class TestCloakbrowserParserISBN:
    """Tests that require ISBN-bearing HTML."""

    def test_isbn13_extracted_into_raw_isbns(self):
        candidates = _parse_search_results(ISBN_HTML, "annas-archive.gl")
        isbns_lists = [c.raw.get("isbns", []) for c in candidates]
        all_isbns = [isbn for lst in isbns_lists for isbn in lst]
        assert all_isbns, "no ISBNs in raw['isbns']"
        assert any(isbn.startswith("978") for isbn in all_isbns)

    def test_isbn13_in_raw_key(self):
        """raw['isbn13'] holds the first ISBN for convenience."""
        candidates = _parse_search_results(ISBN_HTML, "annas-archive.gl")
        isbn13s = [c.raw.get("isbn13") for c in candidates if c.raw.get("isbn13")]
        assert isbn13s, "no isbn13 in raw dict"
        assert isbn13s[0].startswith("978")

    def test_author_extracted_from_filename_hint(self):
        candidates = _parse_search_results(ISBN_HTML, "annas-archive.gl")
        authors = [c.author for c in candidates if c.author]
        assert authors, "no authors extracted"

    def test_publisher_extracted_from_isbn_html(self):
        candidates = _parse_search_results(ISBN_HTML, "annas-archive.gl")
        publishers = [c.publisher for c in candidates if c.publisher]
        assert publishers, "no publishers extracted"
        assert any("Pragmatic" in p or "Addison" in p for p in publishers)


class TestParserFieldCompleteness:
    """Verify all expected fields are non-None for well-formed rows."""

    def test_all_core_fields_populated(self):
        candidates = _parse_search_results(ISBN_HTML, "annas-archive.gl")
        first = candidates[0]
        assert first.md5 is not None
        assert first.title is not None
        assert first.detail_url is not None
        assert first.format is not None
        assert first.year is not None
        assert first.filesize_bytes is not None
        assert first.language is not None
