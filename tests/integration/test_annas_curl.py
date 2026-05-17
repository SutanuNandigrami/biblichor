from __future__ import annotations

from pathlib import Path

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, SearchQuery
from endless_library.scrapers.annas_curl import AnnasArchiveCurl

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "annas"


def _cfg() -> ScrapersCfg:
    return ScrapersCfg(
        order=["annas_curl"],
        enabled={"annas_curl": True},
        format_priority=["epub", "pdf"],
        language="en",
        request_delay_seconds=0,
        slow_download_timeout_seconds=5,
        annas_mirrors=["https://annas-archive.gl"],
    )


def _q() -> SearchQuery:
    return SearchQuery(
        title="The Pragmatic Programmer",
        author="Hunt",
        isbn13=None,
        format_priority=("epub", "pdf"),
        language="en",
    )


def test_search_extracts_md5_and_meta(monkeypatch):
    html = (FIX / "search_pragmatic.html").read_text()

    def fake_get(url, *, headers):
        return 200, html

    s = AnnasArchiveCurl(_cfg(), http_get=fake_get)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    out = s.search(_q())
    assert len(out) == 3
    assert out[0].md5 == "a" * 32
    assert out[0].format == "epub"
    assert out[0].filesize_bytes is not None and out[0].filesize_bytes > 1_000_000
    assert out[0].language == "en"
    assert out[0].year == 2019


def test_resolve_cdn_ready(monkeypatch):
    md5_html = (FIX / "md5_page.html").read_text()
    slow_html = (FIX / "slow_download_ready.html").read_text()
    calls = {"i": 0}

    def fake_get(url, *, headers):
        calls["i"] += 1
        if "/md5/" in url:
            return 200, md5_html
        return 200, slow_html

    s = AnnasArchiveCurl(_cfg(), http_get=fake_get)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cand = Candidate(
        provider="annas",
        md5="a" * 32,
        title="x",
        author=None,
        language="en",
        format="epub",
        filesize_bytes=2_000_000,
        year=2019,
        publisher=None,
        edition_hints="",
        detail_url="https://annas-archive.gl/md5/" + "a" * 32,
    )
    handle = s.resolve_cdn(cand)
    assert handle is not None
    assert handle.url.endswith(".epub")
    assert "annas-cdn" in handle.url


def test_resolve_cdn_countdown_then_ready(monkeypatch):
    md5_html = (FIX / "md5_page.html").read_text()
    cd_html = (FIX / "slow_download_countdown.html").read_text()
    ready_html = (FIX / "slow_download_ready.html").read_text()
    state = {"slow_calls": 0}

    def fake_get(url, *, headers):
        if "/md5/" in url:
            return 200, md5_html
        # First slow_download fetch → countdown, second → ready
        state["slow_calls"] += 1
        return 200, cd_html if state["slow_calls"] == 1 else ready_html

    s = AnnasArchiveCurl(_cfg(), http_get=fake_get)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cand = Candidate(
        provider="annas",
        md5="a" * 32,
        title="x",
        author=None,
        language="en",
        format="epub",
        filesize_bytes=2_000_000,
        year=2019,
        publisher=None,
        edition_hints="",
        detail_url="https://annas-archive.gl/md5/" + "a" * 32,
    )
    handle = s.resolve_cdn(cand)
    assert handle is not None
    assert state["slow_calls"] == 2
