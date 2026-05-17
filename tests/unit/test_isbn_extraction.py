from __future__ import annotations

from endless_library.scrapers.annas_curl import AnnasArchiveCurl


def test_extract_isbn13():
    text = "English [en], epub, 2.3 MB, 2019, Pragmatic ISBN 978-0-13-595705-9 stuff"
    isbns = AnnasArchiveCurl._extract_isbns(text)
    assert "9780135957059" in isbns


def test_extract_isbn10_to_13():
    text = "ISBN 0-13-595705-2 author publisher"
    isbns = AnnasArchiveCurl._extract_isbns(text)
    assert "9780135957059" in isbns


def test_extract_multiple_isbns_dedupe():
    text = "9780062498533 0062498533 9780062498533 9780062498533"
    isbns = AnnasArchiveCurl._extract_isbns(text)
    assert isbns == ["9780062498533"]


def test_no_isbn_returns_empty():
    assert AnnasArchiveCurl._extract_isbns("just some text 2019 abc") == []


def test_avoid_random_long_numbers():
    # 14 digits is not an ISBN
    assert AnnasArchiveCurl._extract_isbns("ID 12345678901234") == []
