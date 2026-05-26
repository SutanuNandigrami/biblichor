"""Phase 6u — KindleBangla bulk source tests."""

from __future__ import annotations

from endless_library.sources.kindlebangla import KindleBangla

CATEGORIES_HTML = """
<html><body>
<a href="category/উপন্যাস">novel</a>
<a href="category/থ্রিলার">thriller</a>
<a href="/category/ছোট-গল্প">short stories</a>
<a href="/about">about</a>
</body></html>
"""

CAT_PAGE1_HTML = """
<html><body>
<div class="card">
  <a href="/book/test-book-one">
    <img alt="Test Book One" src="/cover.jpg" />
    <h3>Test Book One</h3>
    <p>Author One</p>
  </a>
</div>
<div class="card">
  <a href="/book/test-book-two">
    <img alt="Test Book Two" src="/cover.jpg" />
    <h3>Test Book Two</h3>
    <p>Author Two</p>
  </a>
</div>
<div class="pagination"><a href="?page=2">পরবর্তী</a></div>
</body></html>
"""

CAT_PAGE2_HTML = """
<html><body>
<div class="card">
  <a href="/book/test-book-three">
    <h3>Test Book Three</h3>
    <p>Author Three</p>
  </a>
</div>
<div class="pagination"><span>২ / ২</span></div>
</body></html>
"""

EMPTY_CAT_HTML = "<html><body><p>No books</p></body></html>"


def make_fetch(responses: dict[str, str | None]):
    """Return a fetch closure that maps URL -> body."""
    seen: list[str] = []

    def fetch(url: str) -> str | None:
        seen.append(url)
        for key, body in responses.items():
            if key in url:
                return body
        return None

    fetch.seen = seen  # type: ignore[attr-defined]
    return fetch


def test_full_catalog_walks_every_category_and_paginates() -> None:
    src = KindleBangla(
        fetch=make_fetch(
            {
                "/categories": CATEGORIES_HTML,
                # All categories return the same paginated pair for this test
                "/category/উপন্যাস?page=1": CAT_PAGE1_HTML,
                "/category/উপন্যাস?page=2": CAT_PAGE2_HTML,
                "/category/থ্রিলার?page=1": CAT_PAGE1_HTML,
                "/category/থ্রিলার?page=2": CAT_PAGE2_HTML,
                "/category/ছোট-গল্প?page=1": CAT_PAGE1_HTML,
                "/category/ছোট-গল্প?page=2": CAT_PAGE2_HTML,
            }
        ),
        delay_sec=0.0,
    )
    refs = list(src.list_to_read(identifier="full", token=None))
    slugs = {r.source_id for r in refs}
    # 3 unique slugs across all categories — dedup works
    assert slugs == {"test-book-one", "test-book-two", "test-book-three"}
    # All carry source='kindlebangla'
    assert {r.source for r in refs} == {"kindlebangla"}
    # Title + author parsed from cards
    one = next(r for r in refs if r.source_id == "test-book-one")
    assert one.title == "Test Book One"
    assert one.author == "Author One"
    assert one.isbn13 is None


def test_category_filter_limits_to_one_category() -> None:
    fetch = make_fetch(
        {
            "/category/উপন্যাস?page=1": CAT_PAGE1_HTML,
            "/category/উপন্যাস?page=2": CAT_PAGE2_HTML,
        }
    )
    src = KindleBangla(fetch=fetch, delay_sec=0.0)
    refs = list(src.list_to_read(identifier="category:উপন্যাস", token=None))
    # Should not fetch /categories at all
    assert not any("/categories" in u for u in fetch.seen)
    assert {r.source_id for r in refs} == {"test-book-one", "test-book-two", "test-book-three"}


def test_pagination_stops_when_no_next_link() -> None:
    src = KindleBangla(
        fetch=make_fetch(
            {
                "/categories": CATEGORIES_HTML,
                "/category/উপন্যাস?page=1": CAT_PAGE1_HTML,
                "/category/উপন্যাস?page=2": CAT_PAGE2_HTML,
                "/category/থ্রিলার?page=1": EMPTY_CAT_HTML,
                "/category/ছোট-গল্প?page=1": EMPTY_CAT_HTML,
            }
        ),
        delay_sec=0.0,
    )
    refs = list(src.list_to_read(identifier="full", token=None))
    # Empty category contributes zero, others continue
    assert len(refs) == 3


def test_max_pages_cap_respected() -> None:
    src = KindleBangla(
        fetch=make_fetch(
            {
                "/categories": CATEGORIES_HTML,
                "/category/উপন্যাস?page=1": CAT_PAGE1_HTML,
                "/category/উপন্যাস?page=2": CAT_PAGE2_HTML,
                "/category/থ্রিলার?page=1": CAT_PAGE1_HTML,
                "/category/থ্রিলার?page=2": CAT_PAGE2_HTML,
                "/category/ছোট-গল্প?page=1": CAT_PAGE1_HTML,
            }
        ),
        delay_sec=0.0,
        max_pages=1,
    )
    refs = list(src.list_to_read(identifier="full", token=None))
    # Only 1 page walked total — page1 of first cat yields 2 refs, then cap hits
    assert len(refs) == 2


def test_fetch_failure_short_circuits_category() -> None:
    src = KindleBangla(
        fetch=make_fetch(
            {
                "/categories": CATEGORIES_HTML,
                "/category/উপন্যাস?page=1": None,  # simulates HTTP failure
                "/category/থ্রিলার?page=1": CAT_PAGE1_HTML,
                "/category/থ্রিলার?page=2": CAT_PAGE2_HTML,
                "/category/ছোট-গল্প?page=1": EMPTY_CAT_HTML,
            }
        ),
        delay_sec=0.0,
    )
    refs = list(src.list_to_read(identifier="full", token=None))
    # Failed category contributes 0; thriller still yields all 3 unique slugs
    assert {r.source_id for r in refs} == {"test-book-one", "test-book-two", "test-book-three"}


def test_registered_in_sources_registry() -> None:
    from endless_library.sources import registry

    inst = registry.build("kindlebangla")
    assert inst.name == "kindlebangla"


def test_chain_for_source_short_circuits_kindlebangla() -> None:
    from endless_library.config import ScrapersCfg
    from endless_library.scrapers import registry

    cfg = ScrapersCfg()
    chain = registry.chain_for_source(
        cfg, source="kindlebangla", query_title="something", is_pd=False
    )
    assert chain == ["kindlebangla_curl"]


def test_chain_for_source_unchanged_for_other_sources() -> None:
    from endless_library.config import ScrapersCfg
    from endless_library.scrapers import registry

    cfg = ScrapersCfg()
    base = registry.pd_aware_order(cfg, query_title="Atomic Habits", is_pd=False)
    routed = registry.chain_for_source(
        cfg, source="goodreads", query_title="Atomic Habits", is_pd=False
    )
    assert routed == base
