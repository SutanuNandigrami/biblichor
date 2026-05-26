"""Phase 6v.4: never-tested vs broken, in-chain indicator, test-now.

Backend pieces:
  - BenchRunRepo.ever_run / last_run_at distinguish "0% — never tested"
    from "0% — broken" so the SPA can stop misreporting unrun scrapers.
  - GET /api/scrapers includes ever_run, last_run_at, in_chain, and
    corpus_tags per scraper.
  - POST /api/scrapers/{name}/test_now runs ONE bench query (picked
    from the scraper's corpus) and returns the outcome.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from endless_library.bench import BenchOutcome
from endless_library.config import ScrapersCfg
from endless_library.db.bench import BenchRunRepo
from endless_library.db.schema import init_db
from endless_library.web import api as api_mod

# ============ BenchRunRepo.ever_run / last_run_at ============


def test_ever_run_false_when_no_rows(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = BenchRunRepo(db)
    assert repo.ever_run(scraper="annas_curl") is False
    assert repo.last_run_at(scraper="annas_curl") is None


def test_ever_run_true_after_single_record(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = BenchRunRepo(db)
    repo.record(scraper="annas_curl", query="Sapiens", success=True, duration_ms=100)
    assert repo.ever_run(scraper="annas_curl") is True
    assert repo.last_run_at(scraper="annas_curl") is not None
    # Other scrapers still false
    assert repo.ever_run(scraper="kindlebangla_curl") is False


def test_ever_run_true_even_for_failures(tmp_path: Path):
    """A 0% success scraper IS ever_run — the whole point is to tell
    'we tried it and it failed' apart from 'we never tried.'"""
    db = tmp_path / "x.db"
    init_db(db)
    repo = BenchRunRepo(db)
    repo.record(scraper="welib_curl", query="Sapiens", success=False, duration_ms=200)
    assert repo.ever_run(scraper="welib_curl") is True
    assert repo.success_rate(scraper="welib_curl", days=30) == 0.0


# ============ /api/scrapers shape ============


def _build_app(db_path: Path) -> FastAPI:
    """Minimal FastAPI app with just enough deps for /api/scrapers
    and /api/scrapers/{name}/test_now to answer."""
    app = FastAPI()
    cfg = SimpleNamespace(
        scrapers=ScrapersCfg(
            order=["annas_curl", "kindlebangla_curl"],
            enabled={"annas_curl": True, "kindlebangla_curl": True, "welib_curl": False},
        ),
        general=SimpleNamespace(books_dir="/tmp/books"),
        smtp=SimpleNamespace(daily_cap=80),
    )
    deps = SimpleNamespace(
        db_path=db_path,
        cfg=cfg,
        bench=BenchRunRepo(db_path),
        books=SimpleNamespace(pending=lambda **kw: []),
    )
    app.state.deps = deps
    app.state.scheduler = SimpleNamespace(running=True)
    app.state.config_path = Path("/tmp/cfg.yaml")
    api_mod.register(app)
    return app


def test_list_scrapers_includes_never_tested_in_chain_corpus_tags(tmp_path: Path):
    """The /api/scrapers payload must include the four Phase 6v.4 fields
    so the SPA can render the new badges."""
    db = tmp_path / "x.db"
    init_db(db)
    app = _build_app(db)
    client = TestClient(app)
    r = client.get("/api/scrapers")
    assert r.status_code == 200
    body = r.json()
    for key in ("ever_run", "last_run_at", "in_chain", "corpus_tags"):
        assert key in body, f"/api/scrapers missing Phase 6v.4 field: {key}"
    assert "kindlebangla_curl" in body["ever_run"]
    assert body["ever_run"]["kindlebangla_curl"] is False  # nothing recorded yet
    assert body["in_chain"]["annas_curl"] is True  # in order + enabled
    assert body["in_chain"]["welib_curl"] is False  # enabled=false
    # corpus_tags loaded from queries.yaml: kindlebangla_curl is
    # specialised, annas_curl is general (empty list).
    assert body["corpus_tags"]["kindlebangla_curl"] == ["kindlebangla"]
    assert body["corpus_tags"]["annas_curl"] == []


def test_list_scrapers_ever_run_flips_after_record(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    app = _build_app(db)
    client = TestClient(app)
    repo = BenchRunRepo(db)
    repo.record(scraper="annas_curl", query="x", success=True)

    r = client.get("/api/scrapers")
    body = r.json()
    assert body["ever_run"]["annas_curl"] is True
    assert body["ever_run"]["kindlebangla_curl"] is False


# ============ POST /api/scrapers/{name}/test_now ============


def test_test_now_404_for_unknown_scraper(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    app = _build_app(db)
    client = TestClient(app)
    r = client.post("/api/scrapers/no_such_scraper/test_now")
    assert r.status_code == 404
    assert "unknown scraper" in r.json()["detail"]


def test_test_now_runs_one_query_and_records_outcome(tmp_path: Path):
    """The endpoint picks a query the scraper accepts, runs it, and
    persists the outcome so success_rate reflects it on next reload."""
    db = tmp_path / "x.db"
    init_db(db)
    app = _build_app(db)
    client = TestClient(app)

    fake_outcome = BenchOutcome(
        scraper="annas_curl",
        query="The Pragmatic Programmer",
        success=True,
        duration_ms=42,
        candidates=3,
        matched_isbn=True,
        note="",
    )
    # api.py imports run_bench at module top, so patching api_mod.run_bench
    # is the right target. queries_for_scraper is imported inside the
    # function — it's not patched here because it just filters queries.
    with patch.object(api_mod, "run_bench", return_value=[fake_outcome]):
        r = client.post("/api/scrapers/annas_curl/test_now")
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"]["scraper"] == "annas_curl"
    assert body["outcome"]["success"] is True
    assert body["outcome"]["duration_ms"] == 42


def test_test_now_400_when_corpus_has_nothing_matching(tmp_path: Path):
    """If the scraper has corpus_tags but every query lacks the right
    tag, return 400 — running a bench with zero queries is a bug to
    surface, not silently report a phantom 'no candidates' outcome."""
    db = tmp_path / "x.db"
    init_db(db)
    app = _build_app(db)
    client = TestClient(app)

    # Patch the corpus_tags loader to claim kindlebangla_curl needs a
    # tag no query in the bundled corpus carries. The endpoint imports
    # the symbol fresh inside the function, so the patch targets
    # endless_library.bench (the canonical name), not api_mod.
    with patch(
        "endless_library.bench.load_corpus_tags",
        return_value={"kindlebangla_curl": frozenset({"klingon"})},
    ):
        r = client.post("/api/scrapers/kindlebangla_curl/test_now")
    assert r.status_code == 400
    assert "corpus tags" in r.json()["detail"].lower()
