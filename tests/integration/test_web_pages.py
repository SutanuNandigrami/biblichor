from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from endless_library.app import create_app
from endless_library.config import Config
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
    from endless_library.config import save_config

    save_config(cfg, cfg_path)
    app = create_app(deps=deps, config_path=cfg_path)
    return TestClient(app), deps


def test_root_redirects_to_queue(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200
    assert "refresh" in r.text.lower()


def test_queue_page_renders(client):
    c, _ = client
    r = c.get("/queue")
    assert r.status_code == 200
    assert "endless-library" in r.text
    assert "no books" in r.text


def test_add_book_redirects_to_detail(client):
    c, deps = client
    r = c.post("/api/books/add", data={"title": "Test", "author": "A"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/book/")
    assert deps.books.count() == 1


def test_book_detail_renders(client):
    c, deps = client
    bid = deps.books.upsert(title="X", author="Y", isbn13=None, source="manual", source_id="m1")
    r = c.get(f"/book/{bid}")
    assert r.status_code == 200
    assert "X" in r.text


def test_book_404_for_unknown(client):
    c, _ = client
    assert c.get("/book/99999").status_code == 404


def test_retry_changes_status(client):
    c, deps = client
    bid = deps.books.upsert(title="X", author=None, isbn13=None, source="manual", source_id="m1")
    deps.books.set_status(bid, "searching")
    deps.books.set_status(bid, "failed", error="x")
    r = c.post(f"/api/books/{bid}/retry")
    assert r.status_code == 200
    assert deps.books.get(bid).status == "queued"


def test_sources_page_renders(client):
    c, _ = client
    r = c.get("/sources")
    assert r.status_code == 200
    assert "Sources" in r.text


def test_add_source(client):
    c, deps = client
    r = c.post(
        "/api/sources",
        data={"source": "goodreads", "identifier": "12345:to-read"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert len(deps.sources.list_all()) == 1


def test_scrapers_page_renders(client):
    c, _ = client
    r = c.get("/scrapers")
    assert r.status_code == 200
    assert "annas_curl" in r.text


def test_settings_page_renders(client):
    c, _ = client
    r = c.get("/settings")
    assert r.status_code == 200
    assert "SMTP" in r.text


def test_settings_save_persists(client, tmp_path: Path):
    c, deps = client
    r = c.post(
        "/api/settings",
        data={
            "poll_interval_minutes": 30,
            "max_attempts": 2,
            "kindle_recipient": "you@kindle.com",
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_user": "u@example.com",
            "smtp_password": "secret",
            "auto_pick_threshold": 80,
            "auto_pick_gap": 5,
            "log_level": "INFO",
        },
    )
    assert r.status_code == 200
    # In-memory cfg updated
    assert deps.cfg.general.poll_interval_minutes == 30
    assert deps.cfg.smtp.password == "secret"


def test_logs_page_renders(client):
    c, deps = client
    bid = deps.books.upsert(title="x", author=None, isbn13=None, source="manual", source_id="m1")
    deps.events.append(book_id=bid, kind="state_change", message="hello")
    r = c.get("/logs")
    assert r.status_code == 200
    assert "hello" in r.text


def test_healthz(client):
    c, _ = client
    r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True
