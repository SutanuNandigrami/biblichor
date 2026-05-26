from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .schema import connect


@dataclass(frozen=True, slots=True)
class BenchJobRow:
    id: int
    started_at: str
    finished_at: str | None
    mode: str
    status: str
    progress_done: int
    progress_total: int
    summary_json: str | None
    cancel_requested: int = 0  # stored in DB column

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> BenchJobRow:
        return cls(
            id=r["id"],
            started_at=r["started_at"],
            finished_at=r["finished_at"],
            mode=r["mode"],
            status=r["status"],
            progress_done=r["progress_done"],
            progress_total=r["progress_total"],
            summary_json=r["summary_json"],
            cancel_requested=r["cancel_requested"],
        )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class BenchJobsRepo:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def create(self, *, mode: str, progress_total: int) -> int:
        with connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO bench_jobs (started_at, mode, status, progress_total)
                   VALUES (?, ?, 'running', ?)""",
                (_now_iso(), mode, progress_total),
            )
            return int(cur.lastrowid)

    def increment_progress(self, job_id: int) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE bench_jobs SET progress_done = progress_done + 1 WHERE id = ?",
                (job_id,),
            )

    def finish(self, job_id: int, *, status: str, summary_json: str | None = None) -> None:
        if status not in ("done", "cancelled", "failed"):
            raise ValueError(f"invalid bench job status: {status!r}")
        with connect(self.db_path) as conn:
            conn.execute(
                """UPDATE bench_jobs SET status = ?, finished_at = ?, summary_json = ?
                   WHERE id = ?""",
                (status, _now_iso(), summary_json, job_id),
            )

    def request_cancel(self, job_id: int) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE bench_jobs SET cancel_requested = 1 WHERE id = ? AND status = 'running'",
                (job_id,),
            )

    def is_cancel_requested(self, job_id: int) -> bool:
        row = self.get(job_id)
        return row is not None and bool(row.cancel_requested)

    def get(self, job_id: int) -> BenchJobRow | None:
        with connect(self.db_path) as conn:
            r = conn.execute("SELECT * FROM bench_jobs WHERE id = ?", (job_id,)).fetchone()
        return BenchJobRow.from_row(r) if r else None

    def list_recent(self, limit: int = 20) -> list[BenchJobRow]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM bench_jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [BenchJobRow.from_row(r) for r in rows]
