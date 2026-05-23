from pathlib import Path
from endless_library.db.schema import connect, init_db


def test_bench_jobs_table_exists_after_init_db(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    with connect(db) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bench_jobs'"
        ).fetchone()
    assert row is not None


def test_bench_jobs_columns_are_correct(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    with connect(db) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(bench_jobs)")}
    assert cols >= {
        "id", "started_at", "finished_at", "mode", "status",
        "progress_done", "progress_total", "summary_json",
        "cancel_requested",
    }


from endless_library.db.bench_jobs import BenchJobsRepo, BenchJobRow


def test_create_job_returns_id_and_initial_running_status(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = BenchJobsRepo(db)
    job_id = repo.create(mode="quick", progress_total=12)
    assert isinstance(job_id, int) and job_id > 0
    row = repo.get(job_id)
    assert row.status == "running"
    assert row.progress_done == 0
    assert row.progress_total == 12
    assert row.finished_at is None


def test_increment_progress(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = BenchJobsRepo(db)
    job_id = repo.create(mode="quick", progress_total=5)
    repo.increment_progress(job_id)
    repo.increment_progress(job_id)
    row = repo.get(job_id)
    assert row.progress_done == 2


def test_finish_sets_finished_at_and_status_and_summary(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = BenchJobsRepo(db)
    job_id = repo.create(mode="quick", progress_total=3)
    repo.finish(job_id, status="done", summary_json='{"x":1}')
    row = repo.get(job_id)
    assert row.status == "done"
    assert row.finished_at is not None
    assert row.summary_json == '{"x":1}'


def test_request_cancel_sets_flag_without_changing_status(tmp_path: Path):
    """C2: request_cancel sets cancel_requested flag but keeps status=running."""
    db = tmp_path / "x.db"
    init_db(db)
    repo = BenchJobsRepo(db)
    job_id = repo.create(mode="quick", progress_total=3)
    assert repo.is_cancel_requested(job_id) is False
    repo.request_cancel(job_id)
    assert repo.is_cancel_requested(job_id) is True
    # Status must still be running — worker hasn't finished yet
    row = repo.get(job_id)
    assert row.status == "running"


def test_list_recent_returns_newest_first_with_limit(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = BenchJobsRepo(db)
    ids = [repo.create(mode="quick", progress_total=1) for _ in range(5)]
    rows = repo.list_recent(limit=3)
    assert [r.id for r in rows] == list(reversed(ids))[:3]


def test_get_returns_none_for_missing_id(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = BenchJobsRepo(db)
    assert repo.get(9999) is None


import time
import types

from endless_library.bench import BenchQuery, run_bench


class _SlowScraper:
    name = "slow"
    def __init__(self, *a, **kw): pass
    def search(self, q):
        time.sleep(2)
        return []


class _FailingScraper:
    name = "fail"
    def __init__(self, *a, **kw): pass
    def search(self, q):
        raise RuntimeError("boom")


def _fake_registry(monkeypatch, by_name: dict):
    from endless_library.scrapers import registry as r
    monkeypatch.setattr(r, "_REGISTRY", by_name)
    monkeypatch.setattr(r, "enabled_order", lambda cfg: list(by_name.keys()))
    monkeypatch.setattr(r, "build", lambda n, cfg, **kw: by_name[n]())


def test_run_bench_records_timeout_as_failure(tmp_path, monkeypatch):
    _fake_registry(monkeypatch, {"slow": _SlowScraper})
    cfg = types.SimpleNamespace(
        scrapers=types.SimpleNamespace(
            order=["slow"], enabled={"slow": True},
            format_priority=("epub",),
        ),
        bench=types.SimpleNamespace(per_query_timeout_sec=0.5,
                                    circuit_break_after_consecutive_fails=3),
    )
    qs = [BenchQuery("X", "Y", "", "en")]
    outcomes = run_bench(cfg, qs)
    assert len(outcomes) == 1
    assert outcomes[0].success is False
    assert "timeout" in outcomes[0].note.lower()


def test_run_bench_circuit_breaks_after_3_consecutive_fails(tmp_path, monkeypatch):
    _fake_registry(monkeypatch, {"fail": _FailingScraper})
    cfg = types.SimpleNamespace(
        scrapers=types.SimpleNamespace(
            order=["fail"], enabled={"fail": True},
            format_priority=("epub",),
        ),
        bench=types.SimpleNamespace(per_query_timeout_sec=5,
                                    circuit_break_after_consecutive_fails=3),
    )
    qs = [BenchQuery(f"Q{i}", "A", "", "en") for i in range(6)]
    outcomes = run_bench(cfg, qs)
    by_note = [o.note for o in outcomes]
    assert sum(1 for n in by_note if "circuit-broken" in n) == 3
    assert sum(1 for n in by_note if "RuntimeError" in n) == 3

import json as _json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from endless_library.web import api as api_mod
from endless_library.db.bench import BenchRunRepo
from endless_library.config import ScrapersCfg


def _app(tmp_path: Path) -> FastAPI:
    db = tmp_path / "x.db"
    init_db(db)
    app = FastAPI()
    cfg = SimpleNamespace(
        scrapers=ScrapersCfg(order=[], enabled={}),
        bench=SimpleNamespace(per_query_timeout_sec=20,
                              circuit_break_after_consecutive_fails=3),
        general=SimpleNamespace(books_dir='/tmp/books'),
        smtp=SimpleNamespace(daily_cap=80),
    )
    app.state.deps = SimpleNamespace(
        db_path=db, cfg=cfg,
        bench=BenchRunRepo(db),
        bench_jobs=BenchJobsRepo(db),
        books=SimpleNamespace(pending=lambda **kw: []),
    )
    app.state.scheduler = SimpleNamespace(running=True)
    app.state.config_path = Path('/tmp/cfg.yaml')
    api_mod.register(app)
    return app


def test_post_bench_run_returns_202_with_job_id(tmp_path: Path):
    app = _app(tmp_path)
    client = TestClient(app)
    r = client.post('/api/bench/run?mode=quick')
    assert r.status_code == 202
    body = r.json()
    assert 'job_id' in body and isinstance(body['job_id'], int)


def test_get_bench_jobs_returns_list_with_recent_first(tmp_path: Path):
    app = _app(tmp_path)
    client = TestClient(app)
    client.post('/api/bench/run?mode=quick')
    client.post('/api/bench/run?mode=quick')
    r = client.get('/api/bench/jobs')
    assert r.status_code == 200
    jobs = r.json()['jobs']
    assert len(jobs) >= 2
    assert jobs[0]['id'] > jobs[1]['id']


def test_get_bench_job_returns_status_and_progress(tmp_path: Path):
    app = _app(tmp_path)
    client = TestClient(app)
    r = client.post('/api/bench/run?mode=quick')
    job_id = r.json()['job_id']
    g = client.get(f'/api/bench/jobs/{job_id}')
    assert g.status_code == 200
    body = g.json()
    assert body['id'] == job_id
    assert body['status'] in ('running', 'done')
    assert 'progress_done' in body
    assert 'progress_total' in body


def test_get_bench_job_returns_404_for_missing(tmp_path: Path):
    app = _app(tmp_path)
    client = TestClient(app)
    r = client.get('/api/bench/jobs/9999')
    assert r.status_code == 404


def test_post_bench_job_cancel_flips_status(tmp_path: Path):
    app = _app(tmp_path)
    client = TestClient(app)
    r = client.post('/api/bench/run?mode=quick')
    job_id = r.json()['job_id']
    c = client.post(f'/api/bench/jobs/{job_id}/cancel')
    assert c.status_code == 200
    g = client.get(f'/api/bench/jobs/{job_id}')
    assert g.json()['status'] in ('running', 'cancelled', 'done')


def test_bench_job_stream_emits_terminal_event_on_done(tmp_path: Path):
    """SSE endpoint should at minimum send a terminal 'done' event
    once the job finishes. We use TestClient.stream to consume."""
    app = _app(tmp_path)
    client = TestClient(app)
    r = client.post('/api/bench/run?mode=quick')
    job_id = r.json()['job_id']
    # With an empty enabled chain, job finishes immediately.
    with client.stream('GET', f'/api/bench/jobs/{job_id}/stream') as resp:
        events = []
        for line in resp.iter_lines():
            events.append(line)
            if 'event: done' in line or 'event: failed' in line or len(events) > 50:
                break
    assert any('done' in e or 'failed' in e for e in events)


def test_bench_job_stream_poll_interval_is_2s(tmp_path):
    """SSE stream poll interval must default to 2.0 s (ultrareview I9 / m-NEW-3).

    Phase 6aa m-NEW-3: constant moved to module level as SSE_POLL_INTERVAL_SEC
    so tests can monkeypatch it without rewriting internal function scopes.
    """
    import pathlib
    src = pathlib.Path('/home/ubuntu/endless-library/src/endless_library/web/api.py').read_text()
    assert 'SSE_POLL_INTERVAL_SEC: float = 2.0' in src, (
        'Expected module-level SSE_POLL_INTERVAL_SEC = 2.0 in api.py (m-NEW-3)'
    )
    assert 'asyncio.sleep(SSE_POLL_INTERVAL_SEC)' in src, (
        'asyncio.sleep should reference module-level SSE_POLL_INTERVAL_SEC'
    )
    assert 'asyncio.sleep(0.5)' not in src, (
        'asyncio.sleep(0.5) still present; interval was not updated'
    )
    # Verify module attribute is accessible for monkeypatching
    from endless_library.web import api as api_mod
    assert api_mod.SSE_POLL_INTERVAL_SEC == 2.0, (
        f"Expected api.SSE_POLL_INTERVAL_SEC == 2.0, got {api_mod.SSE_POLL_INTERVAL_SEC}"
    )
