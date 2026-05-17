"""Integration: AnnasArchiveFlareSolverr parses captured HTML via mocked FlareSolverr."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, SearchQuery
from endless_library.flaresolverr import FlareSolverrResponse
from endless_library.scrapers.annas_flaresolverr import AnnasArchiveFlareSolverr

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "annas"


def _cfg():
    return ScrapersCfg(
        order=["annas_flaresolverr"],
        enabled={"annas_flaresolverr": True},
        format_priority=["epub", "pdf"],
        language="en",
        request_delay_seconds=0,
        slow_download_timeout_seconds=5,
        annas_mirrors=["https://annas-archive.gl"],
    )


def test_search_via_flaresolverr(monkeypatch):
    html = (FIX / "search_pragmatic.html").read_text()
    fs = MagicMock()
    fs.get.return_value = FlareSolverrResponse(
        status_code=200,
        text=html,
        user_agent="Mozilla",
        cookies=[],
    )
    s = AnnasArchiveFlareSolverr(_cfg(), flaresolverr=fs)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    out = s.search(
        SearchQuery(
            title="The Pragmatic Programmer",
            author="Hunt",
            isbn13=None,
            format_priority=("epub", "pdf"),
            language="en",
        )
    )
    assert len(out) == 3
    assert out[0].md5 == "a" * 32
    assert fs.get.called


def test_resolve_cdn_via_flaresolverr(monkeypatch):
    md5_html = (FIX / "md5_page.html").read_text()
    slow_html = (FIX / "slow_download_ready.html").read_text()
    fs = MagicMock()

    def get(url, **kw):
        text = md5_html if "/md5/" in url else slow_html
        return FlareSolverrResponse(status_code=200, text=text, user_agent="Mozilla", cookies=[])

    fs.get.side_effect = get
    s = AnnasArchiveFlareSolverr(_cfg(), flaresolverr=fs)
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
    h = s.resolve_cdn(cand)
    assert h is not None
    assert "annas-cdn" in h.url


def test_non_ok_returns_none(monkeypatch):
    fs = MagicMock()
    from endless_library.flaresolverr import FlareSolverrError

    fs.get.side_effect = FlareSolverrError("CF challenge failed")
    s = AnnasArchiveFlareSolverr(_cfg(), flaresolverr=fs)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    out = s.search(
        SearchQuery(
            title="x",
            author=None,
            isbn13=None,
            format_priority=("epub",),
            language="en",
        )
    )
    assert out == []
