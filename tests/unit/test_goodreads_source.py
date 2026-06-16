"""Unit tests for the Goodreads identifier parser.

User report (book #1324 era, 2026-06-02): pasting a Goodreads URL
into the Sources form produced a 404 with this URL:

  list_rss/goodreads.com/review/list/69278726?shelf=books-movie-english?shelf=to-read

Two bugs: (1) URL form not recognized, (2) shelf forced to to-read
on top of the user's choice. This test suite locks down both.
"""
from __future__ import annotations

import pytest

from endless_library.sources.goodreads import (
    GOODREADS_RSS,
    GoodreadsRSS,
    _parse_goodreads_identifier,
)

# ---- parser ----


def test_parser_plain_user_id_defaults_to_to_read():
    assert _parse_goodreads_identifier("69278726") == ("69278726", "to-read")


def test_parser_colon_form():
    assert _parse_goodreads_identifier("69278726:read") == ("69278726", "read")


def test_parser_colon_form_with_dashed_shelf():
    assert _parse_goodreads_identifier("69278726:books-movie-english") == (
        "69278726",
        "books-movie-english",
    )


def test_parser_full_url_with_https():
    assert _parse_goodreads_identifier(
        "https://www.goodreads.com/review/list/69278726?shelf=books-movie-english"
    ) == ("69278726", "books-movie-english")


def test_parser_full_url_without_scheme():
    """User pasted this exact form on 2026-06-02 -> 404 before fix."""
    assert _parse_goodreads_identifier(
        "goodreads.com/review/list/69278726?shelf=books-movie-english"
    ) == ("69278726", "books-movie-english")


def test_parser_url_without_shelf_query_defaults_to_to_read():
    assert _parse_goodreads_identifier(
        "https://www.goodreads.com/review/list/69278726"
    ) == ("69278726", "to-read")


def test_parser_user_show_url_form():
    """User profile URL form: /user/show/{id}"""
    assert _parse_goodreads_identifier(
        "https://www.goodreads.com/user/show/69278726-sutanu"
    ) == ("69278726", "to-read")


def test_parser_strips_whitespace():
    assert _parse_goodreads_identifier("  69278726  ") == ("69278726", "to-read")


def test_parser_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        _parse_goodreads_identifier("")
    with pytest.raises(ValueError, match="empty"):
        _parse_goodreads_identifier("   ")


def test_parser_unrecognized_url_raises_actionable_error():
    """A URL that's neither goodreads.com nor a numeric tail must be rejected.
    With host validation, this now produces the host-refusal message
    rather than the older 'could not extract user id'."""
    with pytest.raises(ValueError, match="Refusing non-Goodreads host"):
        _parse_goodreads_identifier("https://example.com/nothing/here")


def test_parser_rejects_non_goodreads_host_even_when_path_ends_in_digits():
    """Without host validation, the parser would accept
    `https://example.com/review/list/12345` (path looks Goodreads-ish)
    and turn it into a Goodreads RSS poll URL. Lock in the rejection
    so a future refactor can't reopen that hole."""
    with pytest.raises(ValueError, match="Refusing non-Goodreads host"):
        _parse_goodreads_identifier("https://example.com/review/list/12345")


def test_parser_accepts_goodreads_subdomains():
    """`www.goodreads.com` and `m.goodreads.com` must still be accepted."""
    assert _parse_goodreads_identifier("https://www.goodreads.com/review/list/69278726") == (
        "69278726", "to-read",
    )
    assert _parse_goodreads_identifier("https://m.goodreads.com/review/list/69278726") == (
        "69278726", "to-read",
    )


# ---- end-to-end on list_to_read ----


def test_list_to_read_builds_correct_url_for_url_identifier():
    """Regression for the 2026-06-02 bug: full URL identifier must
    produce a clean RSS URL with the user's shelf, not a duplicated
    ?shelf= chain."""
    seen_url: list[str] = []

    def _fake_fetch(url: str) -> str:
        seen_url.append(url)
        return "<rss></rss>"  # empty feed; parser yields nothing

    src = GoodreadsRSS(fetch=_fake_fetch)
    list(src.list_to_read(
        identifier="goodreads.com/review/list/69278726?shelf=books-movie-english",
        token=None,
    ))
    assert seen_url == [GOODREADS_RSS.format(user_id="69278726", shelf="books-movie-english")]
    # Specifically: NOT the broken `?shelf=...?shelf=to-read` form
    assert "?shelf=to-read" not in seen_url[0]
    assert seen_url[0].count("?shelf=") == 1


def test_list_to_read_url_does_not_inject_to_read_when_user_supplied_shelf():
    seen_url: list[str] = []
    src = GoodreadsRSS(fetch=lambda u: (seen_url.append(u), "<rss></rss>")[1])
    list(src.list_to_read(identifier="69278726:read", token=None))
    assert seen_url[0].endswith("?shelf=read")


def test_list_to_read_plain_id_defaults_to_to_read():
    seen_url: list[str] = []
    src = GoodreadsRSS(fetch=lambda u: (seen_url.append(u), "<rss></rss>")[1])
    list(src.list_to_read(identifier="69278726", token=None))
    assert seen_url[0].endswith("?shelf=to-read")


# ============ ISBN backfill from detail page (PR #28) ============


_FAKE_HTML_WITH_ISBN = """
<html>
  <head>
    <script type="application/ld+json">
      { "@type": "Book", "name": "Foo", "isbn": "9780063356580" }
    </script>
  </head>
  <body>Goodreads book page</body>
</html>
"""

_FAKE_HTML_NO_ISBN = """
<html><head><title>Book without ISBN</title></head>
<body>Goodreads page without ISBN metadata</body></html>
"""


def test_fetch_isbn_finds_isbn_in_jsonld(monkeypatch):
    seen = []
    def _fake(url):
        seen.append(url)
        return _FAKE_HTML_WITH_ISBN
    src = GoodreadsRSS(fetch=_fake)
    assert src.fetch_isbn("199534613") == "9780063356580"
    assert seen == ["https://www.goodreads.com/book/show/199534613"]


def test_fetch_isbn_returns_none_when_jsonld_missing():
    src = GoodreadsRSS(fetch=lambda u: _FAKE_HTML_NO_ISBN)
    assert src.fetch_isbn("12345") is None


def test_fetch_isbn_swallows_fetch_errors():
    def boom(url):
        raise RuntimeError("network down")
    src = GoodreadsRSS(fetch=boom)
    # Should not raise — best-effort, fall back to None
    assert src.fetch_isbn("12345") is None


def test_list_to_read_backfills_isbn_when_rss_missing_it():
    """The whole point of this feature: a real Goodreads RSS without
    `isbn` should produce a BookRef with isbn13 populated from the
    detail page."""
    rss_xml = """<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title>Margo's Got Money Troubles</title>
        <author>Rufi Thorpe</author>
        <book_id>199534613</book_id>
        <isbn></isbn>
      </item>
    </channel></rss>
    """

    def _fake(url: str) -> str:
        if "list_rss" in url:
            return rss_xml
        if "/book/show/" in url:
            return _FAKE_HTML_WITH_ISBN
        raise ValueError(f"unexpected url: {url}")

    src = GoodreadsRSS(fetch=_fake, fetch_isbn=True)
    refs = list(src.list_to_read(identifier="99999", token=None))
    assert len(refs) == 1
    assert refs[0].isbn13 == "9780063356580"


def test_list_to_read_keeps_rss_isbn_when_present_and_skips_detail_fetch():
    """If RSS already has an ISBN, we don't waste an HTTP call on the
    detail page."""
    rss_xml = """<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title>Some Book</title>
        <book_id>123</book_id>
        <isbn>9780063356580</isbn>
      </item>
    </channel></rss>
    """
    fetches = []
    def _fake(url):
        fetches.append(url)
        if "list_rss" in url:
            return rss_xml
        raise AssertionError(f"unexpected detail-page fetch: {url}")
    src = GoodreadsRSS(fetch=_fake, fetch_isbn=True)
    refs = list(src.list_to_read(identifier="99999", token=None))
    assert refs[0].isbn13 == "9780063356580"
    # Only the RSS URL was fetched; no detail-page call happened.
    assert len(fetches) == 1
    assert "list_rss" in fetches[0]


def test_list_to_read_with_fetch_isbn_disabled_skips_detail_fetch():
    """fetch_isbn=False (e.g. for tests or polite-mode polling) skips
    the per-book detail fetch even when RSS isbn is empty."""
    rss_xml = """<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title>X</title>
        <book_id>123</book_id>
      </item>
    </channel></rss>
    """
    fetches = []
    src = GoodreadsRSS(
        fetch=lambda u: (fetches.append(u), rss_xml)[1] if "list_rss" in u else (_ for _ in ()).throw(AssertionError("should not fetch detail page")),
        fetch_isbn=False,
    )
    refs = list(src.list_to_read(identifier="99999", token=None))
    assert refs[0].isbn13 is None


def test_list_to_read_skips_isbn_fetch_for_non_numeric_book_ids():
    """Some entries fall back to using the entry title as source_id
    (no numeric book_id). Don't try to fetch ISBN with a title-as-id."""
    rss_xml = """<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title>some-title-only-entry</title>
      </item>
    </channel></rss>
    """
    fetches = []
    def _fake(url):
        fetches.append(url)
        return rss_xml if "list_rss" in url else ""
    src = GoodreadsRSS(fetch=_fake, fetch_isbn=True)
    refs = list(src.list_to_read(identifier="99999", token=None))
    # source_id is the title (not numeric) -> no detail-page fetch.
    assert refs[0].isbn13 is None
    assert len(fetches) == 1
