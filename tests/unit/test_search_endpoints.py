"""Tests for the /api/search and /api/books/from-search endpoints.

Covers:
- cover_url extraction in annas_parsing (positive + negative)
- search endpoint happy path, in_library flagging, empty query 400,
  scraper exception handling
- from-search creates book + candidate + sets picked_candidate_id
- from-search is idempotent on existing md5
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from endless_library.db.books import BookRepo
from endless_library.db.candidates import CandidateRepo
from endless_library.db.schema import init_db
from endless_library.domain.models import Candidate
from endless_library.scrapers.annas_parsing import parse_search_results
from endless_library.web import api as api_mod

# ============ cover_url extraction (pure parser) =============================


def test_cover_url_extracted_when_img_in_row():
    """An <img src> inside the result row's container should land in
    Candidate.raw['cover_url']."""
    html = """
    <html><body>
    <div class="border-b">
      <a class="js-vim-focus" href="/md5/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">A title</a>
      <img src="https://covers.example/abc.jpg" />
      <span>english</span>
      <span>epub, 1.2 MB</span>
    </div>
    </body></html>
    """
    results = parse_search_results(html, "https://annas-archive.gl", max_results=5)
    assert len(results) == 1
    assert results[0].raw["cover_url"] == "https://covers.example/abc.jpg"


def test_cover_url_none_when_no_img():
    """No <img> in the row -> cover_url is None, not raised."""
    html = """
    <html><body>
    <div class="border-b">
      <a class="js-vim-focus" href="/md5/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb">Title without cover</a>
      <span>english</span>
      <span>epub, 1.2 MB</span>
    </div>
    </body></html>
    """
    results = parse_search_results(html, "https://annas-archive.gl", max_results=5)
    assert len(results) == 1
    assert results[0].raw["cover_url"] is None


def test_cover_url_only_kept_when_absolute_http():
    """Relative srcs (data:, /img/foo) should be ignored — covers are
    served from a separate CDN. We only keep http(s)."""
    html = """
    <html><body>
    <div class="border-b">
      <a class="js-vim-focus" href="/md5/cccccccccccccccccccccccccccccccc">T</a>
      <img src="/static/placeholder.png" />
    </div>
    </body></html>
    """
    results = parse_search_results(html, "https://annas-archive.gl", max_results=5)
    assert results[0].raw["cover_url"] is None


# ============ search endpoint =================================================


def _make_app(tmp_path: Path) -> tuple[FastAPI, BookRepo, CandidateRepo]:
    db = tmp_path / "test.db"
    init_db(db)
    books = BookRepo(db)
    cands = CandidateRepo(db)

    app = FastAPI()
    app.state.deps = SimpleNamespace(
        db_path=db,
        cfg=SimpleNamespace(
            scrapers=SimpleNamespace(
                format_priority=("epub", "pdf"),
                language="en",
                order=["annas_curl"],
                enabled={"annas_curl": True},
            ),
            bookorbit=SimpleNamespace(enabled=False, url=""),
        ),
        books=books,
        cands=cands,
    )
    app.state.scheduler = SimpleNamespace(running=True)
    api_mod.register(app)
    return app, books, cands


def _fake_candidate(md5: str, *, title: str = "X", cover: str | None = None) -> Candidate:
    raw = {"isbn13": None, "row_text": "", "isbns": [], "cover_url": cover}
    return Candidate(
        provider="annas",
        md5=md5,
        title=title,
        author="A",
        language="en",
        format="epub",
        filesize_bytes=1_000_000,
        year=2020,
        publisher=None,
        edition_hints="",
        detail_url=f"https://annas-archive.gl/md5/{md5}",
        raw=raw,
    )


def test_search_returns_candidates_with_cover_and_in_library(tmp_path):
    """Happy path: scraper returns 2 candidates, one is already in books
    table -> in_library populated for that row, None for the other."""
    app, books, _ = _make_app(tmp_path)

    md5_a = "a" * 32
    md5_b = "b" * 32
    books.upsert(title="A", author="X", isbn13=None, source="manual", source_id="t1")
    # Manually attach md5 to the existing row so the endpoint's lookup finds it.
    import sqlite3

    conn = sqlite3.connect(app.state.deps.db_path)
    conn.execute("UPDATE books SET md5 = ?, status = 'kindled' WHERE id = 1", (md5_a,))
    conn.commit()
    conn.close()

    fake = [
        _fake_candidate(md5_a, title="A", cover="http://covers/a.jpg"),
        _fake_candidate(md5_b, title="B"),
    ]

    with patch(
        "endless_library.scrapers.registry.build", return_value=MagicMock(search=lambda q: fake)
    ):
        r = TestClient(app).get("/api/search?q=anything&limit=10")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    a_row = next(x for x in body["results"] if x["md5"] == md5_a)
    b_row = next(x for x in body["results"] if x["md5"] == md5_b)
    assert a_row["cover_url"] == "http://covers/a.jpg"
    assert a_row["in_library"] == {"id": 1, "status": "kindled"}
    assert b_row["in_library"] is None


def test_search_empty_query_returns_400(tmp_path):
    app, _, _ = _make_app(tmp_path)
    r = TestClient(app).get("/api/search?q=")
    assert r.status_code == 400


def test_search_short_query_returns_400(tmp_path):
    """Queries shorter than 3 chars are rejected — they would match
    millions of Anna\'s rows and are not useful for picking a book.
    Frontend enforces this too, but the API must guard independently."""
    app, _, _ = _make_app(tmp_path)
    r = TestClient(app).get("/api/search?q=ed")
    assert r.status_code == 400
    assert "3 characters" in r.text or "at least" in r.text


def test_search_scraper_exception_is_skipped_not_500(tmp_path):
    """Fan-out: one broken scraper must NOT 500 the whole response.
    Its name lands in sources_skipped; healthy scrapers still produce
    results. Guarantees a flaky upstream cannot take down the search bar."""
    app, _, _ = _make_app(tmp_path)
    bad_scraper = MagicMock(search=MagicMock(side_effect=RuntimeError("upstream down")))
    with patch("endless_library.scrapers.registry.build", return_value=bad_scraper):
        r = TestClient(app).get("/api/search?q=cairo")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["sources_used"] == []
    assert len(body["sources_skipped"]) >= 1
    assert "RuntimeError" in body["sources_skipped"][0]["reason"]


def test_search_returns_lang_and_source_metadata(tmp_path):
    """The response surfaces which scrapers actually contributed (so the
    UI can show 'searched: annas, doab') plus the effective language."""
    app, _, _ = _make_app(tmp_path)
    fake = [_fake_candidate("f" * 32, title="Hit")]
    with patch(
        "endless_library.scrapers.registry.build", return_value=MagicMock(search=lambda q: fake)
    ):
        r = TestClient(app).get("/api/search?q=cairo&lang=bn")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lang"] == "bn"
    assert "annas_curl" in body["sources_used"]
    assert body["count"] == 1


def test_search_lang_all_falls_back_to_cfg_default(tmp_path):
    """lang=all means 'don't filter by language' — the endpoint normalises
    to the cfg default for the SearchQuery passed to scrapers. The
    response's lang field shows what was actually used."""
    app, _, _ = _make_app(tmp_path)
    fake = [_fake_candidate("a" * 32)]
    with patch(
        "endless_library.scrapers.registry.build", return_value=MagicMock(search=lambda q: fake)
    ):
        r = TestClient(app).get("/api/search?q=cairo&lang=all")
    body = r.json()
    assert body["lang"] == "en"


# ============ from-search endpoint ============================================


def test_from_search_creates_book_and_picks_candidate(tmp_path):
    """A POST creates a books row (source='manual', status='queued'),
    a candidate row, and sets picked_candidate_id so the pipeline
    honors the manual choice."""
    app, _books, _cands = _make_app(tmp_path)

    payload = {
        "md5": "d" * 32,
        "title": "Test Book",
        "author": "Test Author",
        "language": "en",
        "format": "epub",
        "filesize_bytes": 2_345_678,
        "year": 2023,
        "detail_url": "https://annas-archive.gl/md5/" + "d" * 32,
        "provider": "annas",
    }
    r = TestClient(app).post("/api/books/from-search", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    assert body["status"] == "queued"
    book_id = body["book_id"]
    cand_id = body["candidate_id"]

    # books row exists with picked_candidate_id wired up
    import sqlite3

    conn = sqlite3.connect(app.state.deps.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    assert row["status"] == "queued"
    assert row["source"] == "manual"
    assert row["md5"] == payload["md5"]
    assert row["picked_candidate_id"] == cand_id

    # candidate row matches payload
    cand_row = conn.execute("SELECT * FROM candidates WHERE id = ?", (cand_id,)).fetchone()
    assert cand_row["md5"] == payload["md5"]
    assert cand_row["provider"] == payload["provider"]
    conn.close()


def test_from_search_idempotent_on_existing_md5(tmp_path):
    """Re-POSTing the same md5 must return created=False and leave the
    existing book unchanged. This protects the SPA's double-click +
    network-retry case."""
    app, _, _ = _make_app(tmp_path)
    payload = {
        "md5": "e" * 32,
        "title": "Once",
        "detail_url": "https://annas-archive.gl/md5/" + "e" * 32,
    }
    r1 = TestClient(app).post("/api/books/from-search", json=payload)
    assert r1.status_code == 200
    assert r1.json()["created"] is True

    r2 = TestClient(app).post("/api/books/from-search", json=payload)
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["created"] is False
    assert body2["book_id"] == r1.json()["book_id"]
    assert "already tracked" in body2["message"]


# ============ post-PR-6 regression: pool timeout no longer 500s =============


def test_default_search_uses_annas_only_not_full_fanout(tmp_path):
    """PR #6 made the default search hit every enabled scraper, taking
    ~9s per keystroke. Default behaviour reverted to Anna's-only;
    multi-source is opt-in via explicit ?sources=. This test pins the
    default so we don't regress UX into a 9-second-per-letter search."""
    app, _, _ = _make_app(tmp_path)
    builds: list[str] = []

    def _fake_build(name, sc, **kwargs):
        builds.append(name)
        return MagicMock(search=lambda q: [])

    with patch("endless_library.scrapers.registry.build", side_effect=_fake_build):
        r = TestClient(app).get("/api/search?q=anything")
    assert r.status_code == 200
    body = r.json()
    # Only annas_curl should have been built.
    assert builds == ["annas_curl"], f"default fan-out leaked: built {builds}"
    assert body["sources_used"] == [] or body["sources_used"] == ["annas_curl"]


def test_slow_scraper_does_not_500_the_endpoint(tmp_path):
    """If a scraper hangs past per_scraper_timeout, concurrent.futures'
    as_completed() raises TimeoutError from the loop iterator itself.
    The fast-path patch wraps that in try/except so we never 500 — we
    return partial results from whoever finished + flag the slow one
    as skipped. Regression for PR #6 which exploded with TimeoutError."""
    import time as _time

    app, _, _ = _make_app(tmp_path)

    def _slow_search(_q):
        _time.sleep(
            6
        )  # comfortably past the 4s outer as_completed budget (3s per_scraper + 1s grace)
        return []

    # Explicit ?sources= triggers fan-out across multiple scrapers.
    with patch(
        "endless_library.scrapers.registry.build",
        return_value=MagicMock(search=_slow_search),
    ):
        r = TestClient(app).get(
            "/api/search?q=anything&sources=annas_curl,doab",
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 0
    # Both scrapers should appear as skipped (they hung past the budget)
    assert len(body["sources_skipped"]) >= 1


# ============ lang-aware augmentation + post-filter ============================


def _fake_cand_bn(md5: str, *, title: str) -> Candidate:
    """Bengali-script-titled candidate (Annas often returns language=None)."""
    return Candidate(
        provider="annas",
        md5=md5,
        title=title,
        author=None,
        language=None,
        format="epub",
        filesize_bytes=500_000,
        year=2020,
        publisher=None,
        edition_hints="",
        detail_url=f"https://annas-archive.gl/md5/{md5}",
        raw={"isbn13": None, "row_text": "", "isbns": [], "cover_url": None},
    )


def test_search_with_bengali_lang_runs_both_augmented_and_transliterated(tmp_path):
    """User picks Bengali AND types Latin 'kriya yoga'. Endpoint should
    (a) augment one query to 'kriya yoga bengali' (catches English-titled
        Bengali translations),
    (b) ALSO run the transliterated query 'ক্রিয়া যোগ' (catches native-
        script Bengali books — the actual goal),
    (c) post-filter results to only Bengali-script / lang=bn entries.
    """
    app, _, _ = _make_app(tmp_path)
    seen_query: list[str] = []
    fake = [
        _fake_candidate("a" * 32, title="Kriya Yoga English Manual"),
        _fake_cand_bn("b" * 32, title="ক্রিয়াযোগ"),
        _fake_candidate("c" * 32, title="Self-Realization Fellowship Lessons"),
    ]

    class _SpyScraper:
        def search(self, query):
            seen_query.append(query.title)
            return fake

    with patch("endless_library.scrapers.registry.build", return_value=_SpyScraper()):
        r = TestClient(app).get("/api/search?q=kriya%20yoga&lang=bn")

    assert r.status_code == 200, r.text
    body = r.json()
    # Both queries fire (order may vary by thread scheduling)
    assert set(seen_query) == {"kriya yoga bengali", "ক্রিয়া যোগ"}, seen_query
    # Post-filter: only the Bengali-script candidate made it through
    assert body["count"] == 1
    assert body["results"][0]["md5"] == "b" * 32
    assert body["language_filter_applied"] is True
    # Response surfaces both for SPA introspection
    assert body["augmented_query"] == "kriya yoga bengali"
    assert body["transliterated_query"] == "ক্রিয়া যোগ"


def test_search_with_lang_all_does_not_augment_or_filter(tmp_path):
    """lang=all is a no-op: no query augmentation, no script post-filter."""
    app, _, _ = _make_app(tmp_path)
    seen: list[str] = []
    fake = [_fake_candidate("a" * 32, title="anything")]

    class _SpyScraper:
        def search(self, query):
            seen.append(query.title)
            return fake

    with patch("endless_library.scrapers.registry.build", return_value=_SpyScraper()):
        r = TestClient(app).get("/api/search?q=kriya%20yoga&lang=all")

    assert seen == ["kriya yoga"]
    body = r.json()
    assert body["language_filter_applied"] is False
    assert body["count"] == 1


def test_search_bengali_filter_falls_back_when_zero_matches(tmp_path):
    """If the script post-filter would drop EVERY result, we fall back to
    unfiltered output so the user sees something. The flag tells the SPA
    to show a 'no exact lang matches' hint."""
    app, _, _ = _make_app(tmp_path)
    fake = [_fake_candidate("a" * 32, title="English Only Result")]
    with patch(
        "endless_library.scrapers.registry.build",
        return_value=MagicMock(search=lambda q: fake),
    ):
        r = TestClient(app).get("/api/search?q=foobar&lang=bn")
    body = r.json()
    assert body["count"] == 1
    assert body["language_filter_applied"] is False


def test_search_transliteration_does_not_fire_for_native_script_input(tmp_path):
    """If the user already typed Bengali script, no transliteration —
    the input IS the native form. Only one query goes to Anna's."""
    app, _, _ = _make_app(tmp_path)
    seen_query: list[str] = []
    fake = [_fake_cand_bn("a" * 32, title="রবীন্দ্রনাথ")]

    class _SpyScraper:
        def search(self, query):
            seen_query.append(query.title)
            return fake

    with patch("endless_library.scrapers.registry.build", return_value=_SpyScraper()):
        r = TestClient(app).get(
            "/api/search?q=%E0%A6%B0%E0%A6%AC%E0%A7%80%E0%A6%A8%E0%A7%8D%E0%A6%A6%E0%A7%8D%E0%A6%B0%E0%A6%A8%E0%A6%BE%E0%A6%A5&lang=bn"
        )

    assert r.status_code == 200
    body = r.json()
    # Exactly one query, unchanged
    assert seen_query == ["রবীন্দ্রনাথ"]
    assert body["augmented_query"] is None
    assert body["transliterated_query"] is None


def test_search_lang_all_does_not_transliterate(tmp_path):
    """lang=all leaves the query untouched: no augmentation, no
    transliteration, no script post-filter."""
    app, _, _ = _make_app(tmp_path)
    seen: list[str] = []
    fake = [_fake_candidate("a" * 32, title="kriya yoga")]

    class _SpyScraper:
        def search(self, query):
            seen.append(query.title)
            return fake

    with patch("endless_library.scrapers.registry.build", return_value=_SpyScraper()):
        r = TestClient(app).get("/api/search?q=kriya%20yoga&lang=all")

    assert seen == ["kriya yoga"]
    body = r.json()
    assert body["augmented_query"] is None
    assert body["transliterated_query"] is None


def test_search_page_param_threads_to_scraper(tmp_path):
    """The `?page=N` query param must reach the scraper as query.page."""
    app, _, _ = _make_app(tmp_path)
    seen_pages: list[int] = []
    fake = [_fake_candidate("a" * 32, title="X")]

    class _SpyScraper:
        def search(self, query):
            seen_pages.append(query.page)
            return fake

    with patch("endless_library.scrapers.registry.build", return_value=_SpyScraper()):
        r = TestClient(app).get("/api/search?q=anything&page=3")

    assert r.status_code == 200
    assert seen_pages == [3]
    body = r.json()
    assert body["page"] == 3


def test_search_page_clamped_to_max_10(tmp_path):
    """page > 10 is clamped to 10 (10-page depth cap)."""
    app, _, _ = _make_app(tmp_path)
    seen_pages: list[int] = []

    class _SpyScraper:
        def search(self, query):
            seen_pages.append(query.page)
            return []

    with patch("endless_library.scrapers.registry.build", return_value=_SpyScraper()):
        r = TestClient(app).get("/api/search?q=anything&page=99")

    assert r.status_code == 200
    assert seen_pages == [10]
    assert r.json()["page"] == 10


def test_search_page_floored_to_1(tmp_path):
    """page < 1 floors to 1."""
    app, _, _ = _make_app(tmp_path)
    seen_pages: list[int] = []

    class _SpyScraper:
        def search(self, query):
            seen_pages.append(query.page)
            return []

    with patch("endless_library.scrapers.registry.build", return_value=_SpyScraper()):
        r = TestClient(app).get("/api/search?q=anything&page=0")

    assert r.status_code == 200
    assert seen_pages == [1]


def test_search_has_next_true_when_page_is_full(tmp_path):
    """When the scraper returns enough candidates to fill a page,
    has_next must be True (more results likely on next page)."""
    app, _, _ = _make_app(tmp_path)
    # Return 30 candidates — more than the default 25 page size.
    fake = [_fake_candidate(f"{i:032x}", title=f"book {i}") for i in range(30)]
    with patch(
        "endless_library.scrapers.registry.build",
        return_value=MagicMock(search=lambda q: fake),
    ):
        r = TestClient(app).get("/api/search?q=anything&limit=25&page=1")
    body = r.json()
    assert body["count"] == 25
    assert body["has_next"] is True


def test_search_has_next_false_at_page_10_boundary(tmp_path):
    """Even with full results, has_next is False at the 10-page depth."""
    app, _, _ = _make_app(tmp_path)
    fake = [_fake_candidate(f"{i:032x}", title=f"book {i}") for i in range(30)]
    with patch(
        "endless_library.scrapers.registry.build",
        return_value=MagicMock(search=lambda q: fake),
    ):
        r = TestClient(app).get("/api/search?q=anything&limit=25&page=10")
    body = r.json()
    assert body["page"] == 10
    assert body["has_next"] is False
