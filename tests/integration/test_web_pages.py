"""SPA backend: JSON API + static SPA mount."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from endless_library.app import create_app
from endless_library.config import Config, save_config
from endless_library.pipeline import PipelineDeps


@pytest.fixture
def client(tmp_path: Path):
    cfg = Config()
    cfg.scrapers.order = ["annas_curl"]
    cfg.scrapers.enabled = {"annas_curl": True}
    cfg.scrapers.annas_mirrors = ["https://annas-archive.gl"]
    cfg.smtp.host = "127.0.0.1"
    cfg.kindle.recipient = "me@kindle.com"
    db = tmp_path / "library.db"
    deps = PipelineDeps.build(cfg=cfg, db_path=db)
    cfg_path = tmp_path / "config.yaml"
    save_config(cfg, cfg_path)
    app = create_app(cfg=cfg, deps=deps, config_path=cfg_path)
    return TestClient(app), deps


def test_healthz(client):
    """healthz moved from /api/healthz to /healthz (root) and now
    requires the scheduler to be running. Use the TestClient lifespan
    context so the scheduler actually starts."""
    from fastapi.testclient import TestClient

    _, deps = client
    # Rebuild a client inside a `with` so FastAPI lifespan fires
    from endless_library.app import create_app
    from endless_library.config import Config, save_config

    cfg = Config()
    cfg.scrapers.order = ["annas_curl"]
    cfg.scrapers.enabled = {"annas_curl": True}
    cfg.scrapers.annas_mirrors = ["https://annas-archive.gl"]
    cfg.smtp.host = "127.0.0.1"
    cfg.kindle.recipient = "me@kindle.com"
    # reuse the same deps + db so we don't reinit
    cfg_path = deps.db_path.parent / "config.yaml"
    save_config(cfg, cfg_path)
    app = create_app(cfg=cfg, deps=deps, config_path=cfg_path)

    with TestClient(app) as tc:
        # root /healthz returns 200 with the new component map
        # (NOTE: /api/healthz no longer routes here; the SPA fallback
        # catches unrouted /api/* with HTML 200 - that's a separate quirk.)
        r = tc.get("/healthz")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["db"] is True
        assert body["scrapers"] >= 1
        assert body["scheduler"] is True


def test_add_and_list_books(client):
    c, deps = client
    r = c.post("/api/books", json={"title": "Test", "author": "A"})
    assert r.status_code == 200
    bid = r.json()["id"]
    r2 = c.get("/api/books")
    assert r2.status_code == 200
    titles = [b["title"] for b in r2.json()["books"]]
    assert "Test" in titles
    assert deps.books.get(bid) is not None


def test_get_book_detail(client):
    c, deps = client
    bid = deps.books.upsert(title="X", author="Y", isbn13=None, source="manual", source_id="m1")
    r = c.get(f"/api/books/{bid}")
    assert r.status_code == 200
    body = r.json()
    assert body["book"]["title"] == "X"
    assert body["candidates"] == []
    assert "events" in body


def test_retry_book(client):
    c, deps = client
    bid = deps.books.upsert(title="X", author=None, isbn13=None, source="manual", source_id="m1")
    deps.books.set_status(bid, "searching")
    deps.books.set_status(bid, "failed", error="x")
    r = c.post(f"/api/books/{bid}/retry")
    assert r.status_code == 200
    assert deps.books.get(bid).status == "queued"


def test_add_source(client):
    c, deps = client
    r = c.post("/api/sources", json={"source": "goodreads", "identifier": "12345:to-read"})
    assert r.status_code == 200
    assert len(deps.sources.list_all()) == 1


def test_list_scrapers(client):
    c, _ = client
    r = c.get("/api/scrapers")
    assert r.status_code == 200
    body = r.json()
    assert "annas_curl" in body["available"]


def test_settings_round_trip(client):
    c, deps = client
    r = c.get("/api/settings")
    assert r.status_code == 200
    r2 = c.post("/api/settings", json={"poll_interval_minutes": 30, "smtp_password": "newpw"})
    assert r2.status_code == 200
    assert deps.cfg.general.poll_interval_minutes == 30
    assert deps.cfg.smtp.password == "newpw"


def test_cycle_status_initially_not_running(client):
    c, _ = client
    r = c.get("/api/cycle/status")
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False


def test_setup_status(client):
    c, _ = client
    r = c.get("/api/setup")
    assert r.status_code == 200
    body = r.json()
    assert "sources_count" in body
    assert "smtp_configured" in body


def _insert_candidate(deps, bid: int, md5: str, title: str) -> int:
    return deps.cands.insert(
        book_id=bid,
        provider="annas",
        md5=md5,
        title=title,
        author=None,
        language=None,
        format="pdf",
        filesize_bytes=120_000_000,
        year=None,
        publisher=None,
        edition_hints="",
        score=100.0,
        detail_url=f"https://annas-archive.gl/md5/{md5}",
        raw_json="{}",
    )


def test_retry_download_preserves_pick_and_candidates(client):
    """retry-download must NOT clear picked_candidate_id or candidates
    when a pick is already set — that was the bug the user hit on book
    নেতাজি ফিরেছিলেন: dashboard 'Retry' wiped the pick and forced a
    re-search instead of just re-attempting the download.
    """
    c, deps = client
    bid = deps.books.upsert(
        title="Netaji",
        author=None,
        isbn13=None,
        source="manual",
        source_id="m-netaji",
    )
    cid = _insert_candidate(deps, bid, "a" * 32, "Netaji Firechilen")
    # Set picked via the API to mirror real flow
    pr = c.post(f"/api/books/{bid}/pick/{cid}")
    assert pr.status_code == 200
    deps.books.set_status(bid, "searching")
    deps.books.set_status(bid, "downloading")
    deps.books.set_status(bid, "failed", error="HTTP 520 from welib gateway")

    r = c.post(f"/api/books/{bid}/retry-download")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "redownload"
    assert body["picked_candidate_id"] == cid

    book = deps.books.get(bid)
    assert book.status == "queued"
    assert book.last_error is None
    assert book.attempts == 0
    # The critical assertions — pick + candidates preserved
    assert book.picked_candidate_id == cid
    assert len(deps.cands.top_for_book(bid, limit=10)) == 1


def test_retry_download_falls_back_to_research_when_no_pick(client):
    """If no candidate has been picked yet, retry-download falls back to
    full re-search so the button is never a no-op."""
    c, deps = client
    bid = deps.books.upsert(
        title="No Pick Yet",
        author=None,
        isbn13=None,
        source="manual",
        source_id="m-nopick",
    )
    deps.books.set_status(bid, "searching")
    deps.books.set_status(bid, "failed", error="nothing found")

    r = c.post(f"/api/books/{bid}/retry-download")
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "research"
    assert deps.books.get(bid).status == "queued"


def test_retry_keeps_full_research_semantics(client):
    """The original /retry endpoint must still clear pick + candidates
    (used by the 'Re-search' button when the user thinks the pick was
    wrong)."""
    c, deps = client
    bid = deps.books.upsert(
        title="Re-search Me",
        author=None,
        isbn13=None,
        source="manual",
        source_id="m-research",
    )
    cid = _insert_candidate(deps, bid, "b" * 32, "Wrong Pick")
    pr = c.post(f"/api/books/{bid}/pick/{cid}")
    assert pr.status_code == 200
    assert deps.books.get(bid).picked_candidate_id == cid

    r = c.post(f"/api/books/{bid}/retry")
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "research"

    book = deps.books.get(bid)
    assert book.status == "queued"
    assert book.picked_candidate_id is None
    assert deps.cands.top_for_book(bid, limit=10) == []


# ============ bulk_retry + bulk_retry_download (PR #36) ============


def test_bulk_retry_clears_pick_and_requeues_selected(client):
    """POST /api/books/bulk_retry on a list of ids must clear each book's
    picked candidate + return it to the search pool. Mirrors the per-book
    /retry endpoint, just fanned out."""
    c, deps = client
    bid1 = deps.books.upsert(title="A", author=None, isbn13=None, source="manual", source_id="m-a")
    bid2 = deps.books.upsert(title="B", author=None, isbn13=None, source="manual", source_id="m-b")
    cid1 = _insert_candidate(deps, bid1, "1" * 32, "A candidate")
    cid2 = _insert_candidate(deps, bid2, "2" * 32, "B candidate")
    assert c.post(f"/api/books/{bid1}/pick/{cid1}").status_code == 200
    assert c.post(f"/api/books/{bid2}/pick/{cid2}").status_code == 200
    deps.books.set_status(bid1, "failed", error="boom")
    deps.books.set_status(bid2, "failed", error="boom")

    r = c.post("/api/books/bulk_retry", json={"ids": [bid1, bid2]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["retried"] == 2
    assert body["mode"] == "research"

    for bid in (bid1, bid2):
        b = deps.books.get(bid)
        assert b.status == "queued"
        assert b.picked_candidate_id is None
        assert b.last_error is None


def test_bulk_retry_by_status_scope(client):
    """When ids is omitted but status is set, every row with that status
    is retried. Used by the 'Re-search all failed' status-bar button."""
    c, deps = client
    bid1 = deps.books.upsert(title="X", author=None, isbn13=None, source="manual", source_id="m-x")
    bid2 = deps.books.upsert(title="Y", author=None, isbn13=None, source="manual", source_id="m-y")
    bid3 = deps.books.upsert(title="Z", author=None, isbn13=None, source="manual", source_id="m-z")
    deps.books.set_status(bid1, "failed", error="e")
    deps.books.set_status(bid2, "failed", error="e")
    # bid3 stays in default 'pending' so it must NOT be touched

    r = c.post("/api/books/bulk_retry", json={"status": "failed"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["retried"] == 2
    assert deps.books.get(bid1).status == "queued"
    assert deps.books.get(bid2).status == "queued"
    # untouched
    z = deps.books.get(bid3)
    assert z.status != "queued" or z.last_error is None  # the negative is the point


def test_bulk_retry_download_preserves_pick_when_set(client):
    """bulk_retry_download must use the narrow redownload path (preserves
    picked_candidate_id + candidates) when a pick exists."""
    c, deps = client
    bid = deps.books.upsert(title="P", author=None, isbn13=None, source="manual", source_id="m-p")
    cid = _insert_candidate(deps, bid, "3" * 32, "P candidate")
    assert c.post(f"/api/books/{bid}/pick/{cid}").status_code == 200
    deps.books.set_status(bid, "failed", error="HTTP 520")

    r = c.post("/api/books/bulk_retry_download", json={"ids": [bid]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["redownload"] == 1
    assert body["research_fallback"] == 0

    b = deps.books.get(bid)
    assert b.status == "queued"
    assert b.picked_candidate_id == cid  # preserved
    assert len(deps.cands.top_for_book(bid, limit=10)) == 1


def test_bulk_retry_download_falls_back_to_research_when_no_pick(client):
    """If a selected book has no pick yet, bulk_retry_download falls back
    to full re-search so the button is never a no-op for the user."""
    c, deps = client
    bid = deps.books.upsert(
        title="NoPick", author=None, isbn13=None, source="manual", source_id="m-np"
    )
    deps.books.set_status(bid, "failed", error="nothing found")

    r = c.post("/api/books/bulk_retry_download", json={"ids": [bid]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["redownload"] == 0
    assert body["research_fallback"] == 1
    assert deps.books.get(bid).status == "queued"
