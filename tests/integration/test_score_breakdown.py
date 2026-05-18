"""Regression + feature-intact tests for the score_breakdown field on
the book-detail candidates payload (Phase 3d)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from endless_library.app import create_app
from endless_library.config import Config, save_config
from endless_library.domain.models import Candidate
from endless_library.pipeline import PipelineDeps


@pytest.fixture
def client_with_book(tmp_path: Path):
    cfg = Config()
    cfg.scrapers.order = ["annas_curl"]
    cfg.scrapers.enabled = {"annas_curl": True}
    db = tmp_path / "library.db"
    deps = PipelineDeps.build(cfg=cfg, db_path=db)
    cfg_path = tmp_path / "config.yaml"
    save_config(cfg, cfg_path)
    # Seed a book with one candidate
    book_id = deps.books.upsert(
        title="The Pragmatic Programmer",
        author="Hunt",
        isbn13="9780135957059",
        source="manual",
        source_id=None,
    )
    deps.cands.insert(
        book_id=book_id,
        provider="annas",
        md5="a" * 32,
        title="The Pragmatic Programmer",
        author="Hunt",
        language="en",
        format="epub",
        filesize_bytes=2_000_000,
        year=2019,
        publisher=None,
        edition_hints="",
        score=85.0,
        detail_url="https://annas-archive.gl/md5/" + ("a" * 32),
        raw_json='{"isbns": ["9780135957059"]}',
    )
    app = create_app(cfg=cfg, deps=deps, config_path=cfg_path)
    return TestClient(app), book_id


# ============ REGRESSION: score_breakdown is now in the response ============


def test_book_detail_includes_score_breakdown(client_with_book):
    c, book_id = client_with_book
    r = c.get(f"/api/books/{book_id}")
    assert r.status_code == 200
    cands = r.json()["candidates"]
    assert len(cands) == 1
    cand = cands[0]
    assert "score_breakdown" in cand, "score_breakdown missing from candidate response"
    bd = cand["score_breakdown"]
    assert isinstance(bd, dict)


def test_score_breakdown_components_contains_expected_keys(client_with_book):
    """Per Phase 3b, the components dict must include the identity
    signals the drawer renders. Pin them."""
    c, book_id = client_with_book
    r = c.get(f"/api/books/{book_id}")
    bd = r.json()["candidates"][0]["score_breakdown"]
    components = bd["components"]
    EXPECTED = {"isbn_match", "isbn13_matched", "title_similarity", "title_similarity_raw"}
    assert EXPECTED <= set(components.keys()), f"missing: {EXPECTED - set(components.keys())}"


def test_score_breakdown_reflects_actual_isbn_match(client_with_book):
    """The seeded candidate has the book's ISBN — recomputation should
    show isbn13_matched=1.0."""
    c, book_id = client_with_book
    r = c.get(f"/api/books/{book_id}")
    components = r.json()["candidates"][0]["score_breakdown"]["components"]
    assert components["isbn13_matched"] == 1.0
    assert components["isbn_match"] > 0  # weighted value


def test_score_breakdown_title_similarity_near_one_for_perfect_match(client_with_book):
    c, book_id = client_with_book
    r = c.get(f"/api/books/{book_id}")
    components = r.json()["candidates"][0]["score_breakdown"]["components"]
    assert components["title_similarity_raw"] >= 0.95


# ============ FEATURE-INTACT: existing response shape preserved ============


def test_book_detail_response_shape(client_with_book):
    """All the pre-Phase-3d fields must still be present in the response."""
    c, book_id = client_with_book
    r = c.get(f"/api/books/{book_id}")
    body = r.json()
    assert "book" in body and "candidates" in body and "events" in body
    cand = body["candidates"][0]
    REQUIRED = {"id", "provider", "md5", "title", "format", "filesize_bytes",
                "language", "score", "detail_url", "mirror"}
    assert REQUIRED <= set(cand.keys()), f"regressed: missing {REQUIRED - set(cand.keys())}"


def test_book_detail_endpoint_returns_404_for_missing_book(client_with_book):
    c, _ = client_with_book
    r = c.get("/api/books/99999")
    assert r.status_code == 404


def test_score_breakdown_is_safe_on_corrupt_raw_json(tmp_path: Path):
    """If raw_json on disk is corrupted, the endpoint must not 500 —
    just return an `error` key in the breakdown."""
    cfg = Config()
    cfg.scrapers.order = ["annas_curl"]
    cfg.scrapers.enabled = {"annas_curl": True}
    db = tmp_path / "library.db"
    deps = PipelineDeps.build(cfg=cfg, db_path=db)
    cfg_path = tmp_path / "config.yaml"
    save_config(cfg, cfg_path)
    book_id = deps.books.upsert(
        title="x", author="y", isbn13=None, source="manual", source_id=None
    )
    # Insert with broken raw_json
    deps.cands.insert(
        book_id=book_id, provider="annas", md5="b" * 32, title="x", author="y",
        language="en", format="epub", filesize_bytes=1000, year=None,
        publisher=None, edition_hints="", score=10.0,
        detail_url="x", raw_json="not json {{",
    )
    app = create_app(cfg=cfg, deps=deps, config_path=cfg_path)
    c = TestClient(app)
    r = c.get(f"/api/books/{book_id}")
    # Endpoint should not 500 on corrupt raw_json — it falls through to the
    # except block (note: simplejson is lenient, plain json is strict;
    # the recompute may succeed with empty raw or hit our error catch).
    assert r.status_code == 200
    bd = r.json()["candidates"][0]["score_breakdown"]
    # Either we got components (lenient parse), or an error key (strict parse)
    assert "components" in bd or "error" in bd
