from __future__ import annotations

import pytest

from endless_library.scrapers.base import parse_filesize, url_has_book_ext


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://x/y/foo.epub", True),
        ("https://x/y/foo.PDF", True),
        ("https://x/y/foo.epub?token=abc", True),
        ("https://x/y/foo.txt", True),
        ("https://x/y/foo.zip", False),
        ("https://x/y/index.html", False),
    ],
)
def test_url_has_book_ext(url, expected):
    assert url_has_book_ext(url) is expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2.3 MB", 2_411_724),
        ("450 KB", 460_800),
        ("1.2 GB", 1_288_490_188),
        ("500 B", 500),
        ("approx 2.3 MB total", 2_411_724),
        ("", None),
        ("free book", None),
    ],
)
def test_parse_filesize(text, expected):
    actual = parse_filesize(text)
    if expected is None:
        assert actual is None
    else:
        # tolerate floating point rounding
        assert abs(actual - expected) <= 2
