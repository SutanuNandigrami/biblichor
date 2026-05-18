from __future__ import annotations

from endless_library.domain.models import DownloadHandle
from endless_library.download import _filename_from_handle, safe_filename


def test_safe_filename_strips_dangerous():
    out = safe_filename("../etc/passwd")
    # Path-traversal prefix is now stripped entirely (stricter than
    # the pre-Unicode-safe version which kept the leading "..").
    assert not out.startswith("..")
    assert "/" not in out
    assert "etc" in out and "passwd" in out
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


def test_clean_book_filename_strips_annas_suffix():
    from endless_library.download import clean_book_filename

    out = clean_book_filename(
        "The Hate U Give -- Thomas, Angie -- 2017 -- HarperCollins -- "
        "17e6e5706950233c612da1cf3b7e4f7b -- Anna's Archive.epub"
    )
    assert out == "The Hate U Give -- Thomas, Angie -- 2017 -- HarperCollins.epub"


def test_clean_book_filename_curly_apostrophe():
    from endless_library.download import clean_book_filename

    out = clean_book_filename(
        "Title -- Author -- abc123abc123abc123abc123abc12345 -- Anna\u2019s Archive.epub"
    )
    assert out.endswith(".epub")
    assert "abc123abc" not in out
    assert "Anna" not in out


def test_clean_book_filename_no_boilerplate_unchanged():
    from endless_library.download import clean_book_filename

    assert clean_book_filename("normal book.pdf") == "normal book.pdf"


def test_clean_book_filename_only_hash_stripped():
    from endless_library.download import clean_book_filename

    out = clean_book_filename(
        "Pragmatic Programmer -- Hunt -- 66683033570a089fed3cda1cb90a70f9.epub"
    )
    assert out == "Pragmatic Programmer -- Hunt.epub"


def test_filename_from_handle_cleans_urls():
    from endless_library.domain.models import DownloadHandle

    h = DownloadHandle(
        url="https://cdn/x/The%20Hate%20U%20Give%20--%20Thomas%2C%20Angie%20"
        "--%2017e6e5706950233c612da1cf3b7e4f7b%20--%20Anna%E2%80%99s%20Archive.epub",
        headers={},
    )
    name = _filename_from_handle(h, "fallback.epub")
    assert name.endswith(".epub")
    assert "Anna" not in name
    assert "17e6e570" not in name
    assert "The Hate U Give" in name
