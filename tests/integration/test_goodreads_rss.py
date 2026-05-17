from __future__ import annotations

from pathlib import Path

from endless_library.sources.goodreads import GoodreadsRSS

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "goodreads_to_read.xml"


def test_parse_to_read_fixture():
    xml = FIXTURE.read_text(encoding="utf-8")
    src = GoodreadsRSS(fetch=lambda url: xml)
    books = list(src.list_to_read(identifier="12345:to-read", token=None))
    assert len(books) == 3
    titles = [b.title for b in books]
    assert "The Pragmatic Programmer: 20th Anniversary Edition" in titles
    # First book has ISBN-10 — should normalize to 13
    prag = next(b for b in books if b.title.startswith("The Pragmatic"))
    assert prag.isbn13 is not None and len(prag.isbn13) == 13
    assert prag.author == "David Thomas"
    assert prag.source == "goodreads"
    # Third book has no ISBN
    no_isbn = next(b for b in books if b.title == "Untitled Without ISBN")
    assert no_isbn.isbn13 is None


def test_identifier_without_shelf_defaults_to_to_read():
    xml = FIXTURE.read_text(encoding="utf-8")
    fetched = []

    def cap(url):
        fetched.append(url)
        return xml

    src = GoodreadsRSS(fetch=cap)
    list(src.list_to_read(identifier="12345", token=None))
    assert "shelf=to-read" in fetched[0]
