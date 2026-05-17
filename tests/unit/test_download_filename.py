from __future__ import annotations

from endless_library.domain.models import DownloadHandle
from endless_library.download import _filename_from_handle, safe_filename


def test_safe_filename_strips_dangerous():
    assert safe_filename("../etc/passwd").startswith(".._etc")
    assert "/" not in safe_filename("a/b/c")
    assert safe_filename("") == "book"


def test_filename_from_url_with_ext():
    h = DownloadHandle(url="https://x/y/pragmatic.epub?token=abc", headers={})
    assert _filename_from_handle(h, "fallback.epub") == "pragmatic.epub"


def test_filename_fallback_when_no_ext():
    h = DownloadHandle(url="https://x/path/no-ext-here", headers={})
    assert _filename_from_handle(h, "Hunt - Pragmatic.epub") == "Hunt - Pragmatic.epub"


def test_explicit_expected_filename_wins():
    h = DownloadHandle(url="https://x/anything.epub", headers={}, expected_filename="custom.epub")
    assert _filename_from_handle(h, "fallback.epub") == "custom.epub"


def test_safe_filename_preserves_extension_when_truncating():
    name = (
        "The Hate U Give -- Thomas, Angie -- 2017 -- HarperCollins "
        "-- 17e6e5706950233c612da1cf3b7e4f7b -- Anna's Archive.epub"
    )
    out = safe_filename(name)
    assert out.endswith(".epub")
    assert len(out) <= 200


def test_safe_filename_short_unchanged():
    assert safe_filename("book.epub") == "book.epub"


def test_safe_filename_no_extension_just_truncates():
    name = "a" * 250
    out = safe_filename(name)
    assert len(out) == 200
    assert out == "a" * 200


def test_safe_filename_trailing_separator_stripped():
    # The stem might end with separators after truncation; should be stripped
    name = "abc def ghi " * 30 + ".epub"
    out = safe_filename(name, max_length=50)
    assert out.endswith(".epub")
    assert not out[:-5].endswith(" ")
    assert not out[:-5].endswith("_")
