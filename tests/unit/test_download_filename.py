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
