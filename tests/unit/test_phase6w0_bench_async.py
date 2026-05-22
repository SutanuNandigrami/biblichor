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


def test_request_cancel_flips_status(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = BenchJobsRepo(db)
    job_id = repo.create(mode="quick", progress_total=3)
    assert repo.is_cancel_requested(job_id) is False
    repo.request_cancel(job_id)
    assert repo.is_cancel_requested(job_id) is True


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
