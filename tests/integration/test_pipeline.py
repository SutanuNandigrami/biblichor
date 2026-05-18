"""End-to-end: queued book -> sent, with HTTP mocked and aiosmtpd intercepting SMTP."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from aiosmtpd.controller import Controller

from endless_library.config import (
    CalibreCfg,
    Config,
    GeneralCfg,
    KindleCfg,
    PushoverCfg,
    ScoringCfg,
    ScrapersCfg,
    SmtpCfg,
)
from endless_library.pipeline import PipelineDeps, process_one
from endless_library.scrapers import registry as scrapers_registry
from endless_library.scrapers.annas_curl import AnnasArchiveCurl

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "annas"


class _Collector:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append((envelope.rcpt_tos, envelope.content))
        return "250 OK"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def smtp_collector():
    h = _Collector()
    port = _free_port()
    c = Controller(h, hostname="127.0.0.1", port=port)
    c.start()
    yield h, port
    c.stop()


def _fake_search_html() -> str:
    return (FIX / "search_pragmatic.html").read_text()


def _fake_md5_html() -> str:
    return (FIX / "md5_page.html").read_text()


def _fake_slow_ready_html() -> str:
    return (FIX / "slow_download_ready.html").read_text()


def _epub_bytes() -> bytes:
    """Return a minimal but VALID EPUB bytes blob.

    Our new security/unpack layer (commit f10e485++) uses the embedded
    `mimetype` member to distinguish a bare EPUB from a ZIP wrapper that
    needs unpacking. A 4-byte PK header without a real central directory
    is rejected as a malformed archive — exactly what we want in
    production, but the fixture needs to look like a real EPUB.
    """
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        # mimetype must be stored uncompressed per EPUB spec
        z.writestr(
            zipfile.ZipInfo("mimetype", date_time=(2020, 1, 1, 0, 0, 0)),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0"/>',
        )
    return buf.getvalue()


def _make_cfg(books_dir: Path, smtp_port: int) -> Config:
    return Config(
        general=GeneralCfg(
            poll_interval_minutes=1,
            max_attempts=3,
            books_dir=str(books_dir),
            log_level="INFO",
            auto_pick_threshold=10,
            auto_pick_gap=0,
            min_score_for_failure=0,
            zombie_stale_minutes=30,
        ),
        kindle=KindleCfg(recipient="me@kindle.com", subject="{title}", attachment_max_mb=50),
        smtp=SmtpCfg(
            host="127.0.0.1", port=smtp_port, starttls=False, user="local@test", password=""
        ),
        pushover=PushoverCfg(enabled=False),
        calibre=CalibreCfg(enabled=False),
        scrapers=ScrapersCfg(
            order=["annas_curl"],
            enabled={"annas_curl": True},
            format_priority=["epub"],
            language="en",
            request_delay_seconds=0,
            slow_download_timeout_seconds=5,
            annas_mirrors=["https://annas-archive.gl"],
        ),
        scoring=ScoringCfg(
            isbn_match=35,
            title_weight=25,
            author_weight=15,
            format_bonus={"epub": 10, "pdf": 5},
            language_bonus=10,
            filesize_min_bytes=100,
            filesize_max_bytes=80 * 1024 * 1024,
            scan_penalty=10,
            audio_keywords=["audiobook"],
        ),
    )


def test_full_queued_to_sent(tmp_path, smtp_collector, monkeypatch):
    handler, port = smtp_collector
    books_dir = tmp_path / "books"
    db = tmp_path / "library.db"
    cfg = _make_cfg(books_dir, port)
    deps = PipelineDeps.build(cfg=cfg, db_path=db)

    # Seed the DB manually (skip source polling for now)
    bid = deps.books.upsert(
        title="The Pragmatic Programmer",
        author="Hunt",
        isbn13="9780135957059",
        source="manual",
        source_id="m1",
    )

    # Wire fake HTTP into the AnnasArchiveCurl
    search_html = _fake_search_html()
    md5_html = _fake_md5_html()
    slow_html = _fake_slow_ready_html()

    def fake_get(url, *, headers):
        if "/search" in url:
            return 200, search_html
        if "/md5/" in url:
            return 200, md5_html
        if "/slow_download/" in url:
            return 200, slow_html
        return 404, ""

    original_build = scrapers_registry.build

    def patched(name, cfg2, **kw):
        if name == "annas_curl":
            return AnnasArchiveCurl(cfg2, http_get=fake_get)
        return original_build(name, cfg2, **kw)

    monkeypatch.setattr(scrapers_registry, "build", patched)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    # Patch download() so we don't actually hit the network for the CDN URL
    from endless_library import pipeline as pipe_mod
    from endless_library.download import DownloadResult

    def fake_download(handle, *, dest_dir, fallback_name, expected_md5=None, client_factory=None):
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / "pragmatic.epub"
        path.write_bytes(_epub_bytes())
        return DownloadResult(
            path=path, size=len(_epub_bytes()), md5="x" * 32, content_type="application/epub+zip"
        )

    monkeypatch.setattr(pipe_mod, "download", fake_download)

    # Run pipeline
    book = deps.books.get(bid)
    result = process_one(deps, book)
    assert result == "sent", (
        f"expected sent, got {result} (last_error={deps.books.get(bid).last_error})"
    )
    assert len(handler.messages) == 1
    rcpts, body = handler.messages[0]
    assert "me@kindle.com" in rcpts
    assert b"Pragmatic" in body


def test_low_score_routes_to_needs_review(tmp_path, smtp_collector, monkeypatch):
    handler, port = smtp_collector
    books_dir = tmp_path / "books"
    db = tmp_path / "library.db"
    cfg = _make_cfg(books_dir, port)
    # Force auto-pick to never trigger
    cfg.general.auto_pick_threshold = 95
    cfg.general.min_score_for_failure = 1
    deps = PipelineDeps.build(cfg=cfg, db_path=db)

    bid = deps.books.upsert(
        title="The Pragmatic Programmer",
        author="Hunt",
        isbn13=None,
        source="manual",
        source_id="m2",
    )

    def fake_get(url, *, headers):
        if "/search" in url:
            return 200, _fake_search_html()
        return 404, ""

    original_build = scrapers_registry.build
    monkeypatch.setattr(
        scrapers_registry,
        "build",
        lambda name, cfg2, **kw: (
            AnnasArchiveCurl(cfg2, http_get=fake_get)
            if name == "annas_curl"
            else original_build(name, cfg2, **kw)
        ),
    )
    monkeypatch.setattr("time.sleep", lambda *_: None)

    book = deps.books.get(bid)
    result = process_one(deps, book)
    assert result == "needs_review", f"got {result}"
    assert deps.books.get(bid).status == "needs_review"
    # No email sent
    assert handler.messages == []
