from __future__ import annotations

from pathlib import Path

from endless_library.bench import format_table, load_queries, run_bench
from endless_library.config import Config, ScoringCfg, ScrapersCfg
from endless_library.scrapers import registry
from endless_library.scrapers.annas_curl import AnnasArchiveCurl
from endless_library.scrapers.libgen_curl import LibgenCurl
from endless_library.scrapers.welib_curl import WelibCurl

BENCH_FILE = Path(__file__).resolve().parents[2] / "bench" / "queries.yaml"
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "annas"


def test_load_queries():
    qs, quick = load_queries(BENCH_FILE)
    assert len(qs) >= 5
    assert qs[0].title.startswith("The Pragmatic Programmer")
    assert 0 in quick


def test_run_bench_with_fake_scrapers(monkeypatch, tmp_path):
    html = (FIX / "search_pragmatic.html").read_text()

    def fake_get(url, *, headers):
        return 200, html

    # Patch the registry temporarily so that build() returns scrapers using our fake http
    original_build = registry.build

    def patched_build(name, cfg, **kwargs):
        if name == "annas_curl":
            return AnnasArchiveCurl(cfg, http_get=fake_get)
        if name == "welib_curl":
            return WelibCurl(cfg, http_get=lambda u, *, headers: (200, ""))
        if name == "libgen_curl":
            return LibgenCurl(cfg, http_get=lambda u, *, headers: (200, ""))
        return original_build(name, cfg, **kwargs)

    monkeypatch.setattr(registry, "build", patched_build)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    cfg = Config(
        scrapers=ScrapersCfg(
            order=["annas_curl", "welib_curl", "libgen_curl"],
            enabled={"annas_curl": True, "welib_curl": True, "libgen_curl": True},
            annas_mirrors=["https://annas-archive.gl"],
            request_delay_seconds=0,
            format_priority=["epub", "pdf"],
            language="en",
        ),
        scoring=ScoringCfg(),
    )
    qs, _ = load_queries(BENCH_FILE)
    outcomes = run_bench(cfg, qs[:1])
    # 3 strategies x 1 query = 3 outcomes
    assert len(outcomes) == 3
    annas_outs = [o for o in outcomes if o.scraper == "annas_curl"]
    assert annas_outs[0].success is True
    assert annas_outs[0].candidates >= 1


def test_format_table_renders():
    from endless_library.bench import BenchOutcome

    out = format_table(
        [
            BenchOutcome(
                scraper="annas_curl",
                query="X",
                success=True,
                duration_ms=120,
                candidates=3,
                matched_isbn=True,
            ),
            BenchOutcome(
                scraper="annas_curl",
                query="Y",
                success=False,
                duration_ms=900,
                candidates=0,
                matched_isbn=False,
                note="403",
            ),
        ]
    )
    assert "annas_curl" in out
    assert "Avg ms" in out
