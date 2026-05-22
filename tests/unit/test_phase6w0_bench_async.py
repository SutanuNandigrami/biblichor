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
