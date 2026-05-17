from __future__ import annotations

from pathlib import Path

from endless_library.db.schema import EXPECTED_TABLES, connect, init_db


def test_init_db_creates_all_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "library.db"
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    names = {r[0] for r in rows}
    assert "mirrors" in {t for t in EXPECTED_TABLES}
    for t in EXPECTED_TABLES:
        assert t in names, f"missing table: {t}"


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "library.db"
    init_db(db_path)
    init_db(db_path)  # second call must not raise


def test_connect_enables_wal_and_fk(tmp_path: Path) -> None:
    db_path = tmp_path / "library.db"
    init_db(db_path)
    with connect(db_path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
