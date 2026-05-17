from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .schema import connect


@dataclass(frozen=True, slots=True)
class BenchRunRow:
    id: int
    ts: str
    scraper: str
    query: str
    success: bool
    duration_ms: int | None
    http_code: int | None
    notes: str | None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> BenchRunRow:
        return cls(
            id=r["id"],
            ts=r["ts"],
            scraper=r["scraper"],
            query=r["query"],
            success=bool(r["success"]),
            duration_ms=r["duration_ms"],
            http_code=r["http_code"],
            notes=r["notes"],
        )


class BenchRunRepo:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def record(
        self,
        *,
        scraper: str,
        query: str,
        success: bool,
        duration_ms: int | None = None,
        http_code: int | None = None,
        notes: str | None = None,
    ) -> int:
        with connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO bench_runs (scraper, query, success, duration_ms, http_code, notes)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (scraper, query, 1 if success else 0, duration_ms, http_code, notes),
            )
            return int(cur.lastrowid)

    def success_rate(self, *, scraper: str, days: int = 30) -> float:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT AVG(success) AS rate FROM bench_runs
                    WHERE scraper = ? AND ts >= datetime('now', ?)""",
                (scraper, f"-{days} days"),
            ).fetchone()
        return float(row["rate"] or 0.0)

    def recent(self, *, scraper: str | None = None, limit: int = 200) -> list[BenchRunRow]:
        with connect(self.db_path) as conn:
            if scraper:
                rows = conn.execute(
                    "SELECT * FROM bench_runs WHERE scraper = ? ORDER BY id DESC LIMIT ?",
                    (scraper, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM bench_runs ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        return [BenchRunRow.from_row(r) for r in rows]
