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
    from endless_library.domain.models import SearchQuery
    from endless_library.scrapers.bdebooks import BDeBooks

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
    from endless_library.domain.models import SearchQuery
    from endless_library.scrapers.bdebooks import BDeBooks

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


# ============ Task 4: KindleBangla retroactive excluded_categories filter ============

KB_SEARCH_HTML = """
<html><body>
<div class="grid">
  <div class="bg-white rounded-xl">
    <div class="relative h-64">
      <img alt="হিমু সমগ্র-১" src="https://cdn.example.com/cover-1.webp" />
      <div class="absolute"><a href="/book/himu-1">বিস্তারিত</a></div>
    </div>
    <div class="p-4">
      <h3>হিমু সমগ্র-১</h3>
      <p>হুমায়ূন আহমেদ</p>
      <div><span>হিমু সিরিজ</span></div>
    </div>
  </div>
  <div class="bg-white rounded-xl">
    <div class="relative h-64">
      <img alt="কোরআন শরীফ" src="https://cdn.example.com/cover-quran.webp" />
      <div class="absolute"><a href="/book/quran">বিস্তারিত</a></div>
    </div>
    <div class="p-4">
      <h3>কোরআন শরীফ</h3>
      <p>অনুবাদক</p>
      <div><span>Quran</span></div>
    </div>
  </div>
</div>
</body></html>
"""


def test_kindlebangla_filters_excluded_categories_via_search_upstream(monkeypatch):
    from endless_library.domain.models import SearchQuery
    from endless_library.scrapers.kindlebangla_curl import KindleBanglaCurl

    class _KBCfg:
        excluded_categories = ["Quran", "ধর্মীয়"]


    scraper = KindleBanglaCurl(
        _KBCfg(),
        http_get=lambda url: (200, KB_SEARCH_HTML.encode("utf-8")),
    )

    q = SearchQuery(title="হিমু", author=None, isbn13=None,
                    format_priority=("epub",), language="bn")
    hits = scraper.search(q)
    titles = [c.title for c in hits]

    # হিমু should pass filter, কোরআন should be filtered out
    assert "হিমু সমগ্র-১" in titles
    assert not any("কোরআন" in (t or "") for t in titles)


# ============ Task 5: Registry + chain promotion ============


def test_enabled_order_for_query_promotes_bdebooks_for_bengali():
    from endless_library.config import ScrapersCfg
    from endless_library.scrapers.registry import enabled_order_for_query

    cfg = ScrapersCfg(
        order=["annas_curl", "kindlebangla_curl", "bdebooks", "libgen_curl"],
        enabled={
            "annas_curl": True,
            "kindlebangla_curl": True,
            "bdebooks": True,
            "libgen_curl": True,
        },
    )
    order = enabled_order_for_query(cfg, query_title="হিমু সমগ্র")
    # Bengali query → kindlebangla_curl and bdebooks should be promoted to front
    assert order.index("kindlebangla_curl") < order.index("annas_curl")
    assert order.index("bdebooks") < order.index("annas_curl")


# ---------------------------------------------------------------------------
# Ultrareview D: BDeBooks caps detail fetches to _MAX_DETAIL_FETCHES
# ---------------------------------------------------------------------------

def test_bdebooks_caps_detail_fetches_at_max(monkeypatch):
    """search() must fetch at most _MAX_DETAIL_FETCHES detail pages regardless
    of how many results the search page returns."""
    from endless_library.domain.models import SearchQuery
    from endless_library.scrapers.bdebooks import _MAX_DETAIL_FETCHES, BDeBooks

    # Generate 10 articles — more than the cap
    articles = "".join(
        '<article class="post">' +
        f'<h2 class="entry-title"><a class="entry-title" href="https://bdebooks.com/books/book{i}/">Book {i}</a></h2>' +
        '<a href="/category/fiction/" rel="category tag">Fiction</a>' +
        '</article>'
        for i in range(10)
    )
    search_html = f"<html><body>{articles}</body></html>"
    detail_html = '<html><body><a href="https://bdebooks.com/files/x.pdf">Download</a></body></html>'

    fetch_count = [0]

    class _CapClient:
        def get(self, url, **kwargs):
            class _R:
                status_code = 200
            r = _R()
            if "?" in url or url.rstrip("/") == "https://bdebooks.com":
                r.text = search_html
            else:
                fetch_count[0] += 1
                r.text = detail_html
            return r

    monkeypatch.setattr(
        "endless_library.scrapers.bdebooks.make_client",
        lambda **kw: _CapClient(),
    )

    class _Cfg:
        excluded_categories = ()

    b = BDeBooks(cfg=_Cfg())
    cands = b.search(SearchQuery(title="test", author=None, isbn13=None,
                                 format_priority=("pdf",), language="bn"))

    assert len(cands) <= _MAX_DETAIL_FETCHES, (
        f"Expected at most {_MAX_DETAIL_FETCHES} candidates, got {len(cands)}"
    )
    assert fetch_count[0] <= _MAX_DETAIL_FETCHES, (
        f"Expected at most {_MAX_DETAIL_FETCHES} detail fetches, got {fetch_count[0]}"
    )
