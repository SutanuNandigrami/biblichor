"""Phase 6w.6 — BDeBooks + content-filter abstraction tests."""
from __future__ import annotations


# ============ Task 1: Candidate.categories field ============


def test_candidate_categories_default_empty():
    from endless_library.domain.models import Candidate

    c = Candidate(
        provider="annas",
        md5=None,
        title="Test",
        author=None,
        language="en",
        format="epub",
        filesize_bytes=None,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url="https://example.com",
    )
    assert c.categories == ()


def test_candidate_categories_can_be_set():
    from endless_library.domain.models import Candidate

    c = Candidate(
        provider="bdebooks",
        md5=None,
        title="ইসলামিক বই",
        author=None,
        language="bn",
        format="epub",
        filesize_bytes=None,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url="https://bdebooks.com/b/test",
        categories=("ইসলামিক বই", "ধর্ম"),
    )
    assert c.categories == ("ইসলামিক বই", "ধর্ম")


# ============ Task 2: BDeBooks scraper ============

BDEBOOKS_HTML = """
<html><body>
  <article class="post">
    <h2 class="entry-title"><a class="entry-title" href="https://bdebooks.com/books/himu/">হিমু</a></h2>
    <a href="/category/fiction/" rel="category tag">Fiction</a>
  </article>
  <article class="post">
    <h2 class="entry-title"><a class="entry-title" href="https://bdebooks.com/books/quran-translation/">কোরআন</a></h2>
    <a href="/category/islamic-books/" rel="category tag">Islamic Books</a>
  </article>
</body></html>
"""

DETAIL_HTML_WITH_PDF = """
<html><body>
  <a href="https://bdebooks.com/files/himu.pdf">Download PDF</a>
</body></html>
"""


class _FakeClient:
    def __init__(self, search_html, detail_html="<html></html>"):
        self._search_html = search_html
        self._detail_html = detail_html

    def get(self, url, **kwargs):
        class _R:
            status_code = 200
        _r = _R()
        # search page vs detail page
        if "?" in url or url.rstrip("/") == "https://bdebooks.com":
            _r.text = self._search_html
        else:
            _r.text = self._detail_html
        return _r


def test_bdebooks_search_extracts_titles_and_categories(monkeypatch):
    from endless_library.scrapers.bdebooks import BDeBooks
    from endless_library.domain.models import SearchQuery

    monkeypatch.setattr(
        "endless_library.scrapers.bdebooks.make_client",
        lambda **kw: _FakeClient(BDEBOOKS_HTML, DETAIL_HTML_WITH_PDF),
    )

    class _Cfg:
        excluded_categories = ()

    b = BDeBooks(cfg=_Cfg())
    cands = b.search(SearchQuery(title="হিমু", author=None, isbn13=None,
                                 format_priority=("pdf",), language="bn"))
    # Should get himu (has PDF) — quran may or may not appear depending on detail fetch
    assert any("হিমু" in (c.title or "") for c in cands)
    assert all(isinstance(c.categories, tuple) for c in cands)
    # categories populated
    himu_cands = [c for c in cands if "হিমু" in (c.title or "")]
    assert len(himu_cands) >= 1
    assert himu_cands[0].categories == ("Fiction",)


def test_bdebooks_excludes_islamic_when_in_denylist(monkeypatch):
    from endless_library.scrapers.bdebooks import BDeBooks
    from endless_library.domain.models import SearchQuery

    html = """
    <html><body>
      <article class="post">
        <h2 class="entry-title"><a class="entry-title" href="https://bdebooks.com/books/q/">কোরআন</a></h2>
        <a href="/category/islamic-books/" rel="category tag">Islamic Books</a>
      </article>
    </body></html>
    """
    monkeypatch.setattr(
        "endless_library.scrapers.bdebooks.make_client",
        lambda **kw: _FakeClient(html, "<html><body><a href='q.pdf'>q</a></body></html>"),
    )

    class _Cfg:
        excluded_categories = ("Islamic Books", "Religious")

    b = BDeBooks(cfg=_Cfg())
    cands = b.search(SearchQuery(title="কোরআন", author=None, isbn13=None,
                                 format_priority=("pdf",), language="bn"))
    assert cands == []
