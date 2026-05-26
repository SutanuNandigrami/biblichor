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

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endless_library.db.candidates import CandidateRepo
from endless_library.db.schema import init_db
from endless_library.db.books import BookRepo
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

    fake = [_fake_candidate(md5_a, title="A", cover="http://covers/a.jpg"),
            _fake_candidate(md5_b, title="B")]

    with patch("endless_library.scrapers.registry.build", return_value=MagicMock(search=lambda q: fake)):
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
    with patch("endless_library.scrapers.registry.build", return_value=MagicMock(search=lambda q: fake)):
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
    with patch("endless_library.scrapers.registry.build", return_value=MagicMock(search=lambda q: fake)):
        r = TestClient(app).get("/api/search?q=cairo&lang=all")
    body = r.json()
    assert body["lang"] == "en"


# ============ from-search endpoint ============================================


def test_from_search_creates_book_and_picks_candidate(tmp_path):
    """A POST creates a books row (source='manual', status='queued'),
    a candidate row, and sets picked_candidate_id so the pipeline
    honors the manual choice."""
    app, books, cands = _make_app(tmp_path)

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
