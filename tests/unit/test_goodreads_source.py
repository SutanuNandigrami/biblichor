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
    with pytest.raises(ValueError, match="Could not extract Goodreads user id"):
        _parse_goodreads_identifier("https://example.com/nothing/here")


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
