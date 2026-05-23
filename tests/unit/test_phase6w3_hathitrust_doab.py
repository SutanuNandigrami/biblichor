"""Tests for HathiTrust and DOAB scrapers (Phase 6w.3)."""
import pytest


# ============================================================================
# HathiTrust tests
# ============================================================================

def test_hathitrust_returns_pd_candidate_when_isbn_match(monkeypatch):
    from endless_library.scrapers.hathitrust import HathiTrust
    from endless_library.domain.models import SearchQuery
    fake = {
        "records": {
            "9999": {
                "titles": ["Pride and Prejudice"],
                "items": [
                    {"htid": "uc1.123", "rightsCode": "pd"},
                    {"htid": "uc1.456", "rightsCode": "ic-world"},
                ],
            }
        }
    }
    class _R:
        status_code = 200
        def json(self): return fake
        def raise_for_status(self): pass
    monkeypatch.setattr("endless_library.scrapers.hathitrust.make_client",
                        lambda **kw: _FakeSession(_R()))
    ht = HathiTrust(cfg=None)
    cands = ht.search(SearchQuery(title="anything", author="", isbn13="9780486284736",
                                  format_priority=("pdf",), language="en"))
    assert len(cands) == 1
    assert "babel.hathitrust.org" in cands[0].detail_url
    assert cands[0].format == "pdf"


def test_hathitrust_returns_empty_when_no_isbn():
    from endless_library.scrapers.hathitrust import HathiTrust
    from endless_library.domain.models import SearchQuery
    ht = HathiTrust(cfg=None)
    out = ht.search(SearchQuery(title="x", author="", isbn13="",
                                format_priority=("pdf",), language="en"))
    assert out == []


# ============================================================================
# DOAB tests
# ============================================================================

def test_doab_search_extracts_pdf_candidates(monkeypatch):
    from endless_library.scrapers.doab import Doab
    from endless_library.domain.models import SearchQuery
    fake = [
        {
            "metadata": [
                {"key": "dc.title", "value": "Open Book Title"},
                {"key": "dc.creator", "value": "Author Name"},
                {"key": "dc.identifier.uri", "value": "https://example.org/book.pdf"},
            ]
        },
    ]
    class _R:
        status_code = 200
        def json(self): return fake
        def raise_for_status(self): pass
    monkeypatch.setattr("endless_library.scrapers.doab.make_client",
                        lambda **kw: _FakeSession(_R()))
    d = Doab(cfg=None)
    out = d.search(SearchQuery(title="Open Book", author="", isbn13="",
                               format_priority=("pdf",), language="en"))
    assert len(out) == 1
    assert out[0].title == "Open Book Title"
    assert out[0].detail_url.endswith("book.pdf")


def test_doab_includes_language_filter_when_set(monkeypatch):
    from endless_library.scrapers.doab import Doab
    from endless_library.domain.models import SearchQuery
    sent_params = {}
    class _R:
        status_code = 200
        def json(self): return []
        def raise_for_status(self): pass
    class _Sess:
        def get(self, url, params=None, **kw):
            sent_params.update(params or {})
            return _R()
    monkeypatch.setattr("endless_library.scrapers.doab.make_client", lambda **kw: _Sess())
    d = Doab(cfg=None)
    d.search(SearchQuery(title="x", author="", isbn13="",
                         format_priority=("pdf",), language="en"))
    assert "language:en" in sent_params.get("query", "")


# ============================================================================
# Fake session helper for tests
# ============================================================================

class _FakeSession:
    def __init__(self, resp): 
        self._r = resp
    def get(self, url, **kw): 
        return self._r
