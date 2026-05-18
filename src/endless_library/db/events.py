from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import connect


@dataclass(frozen=True, slots=True)
class EventRow:
    id: int
    book_id: int | None
    ts: str
    kind: str
    scraper: str | None
    message: str
    meta: dict[str, Any]

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> EventRow:
        return cls(
            id=r["id"],
            book_id=r["book_id"],
            ts=r["ts"],
            kind=r["kind"],
            scraper=r["scraper"],
            message=r["message"],
            meta=json.loads(r["meta_json"]) if r["meta_json"] else {},
        )


class EventRepo:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def append(
        self,
        *,
        book_id: int | None,
        kind: str,
        message: str,
        scraper: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> int:
        with connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO events (book_id, kind, scraper, message, meta_json) VALUES (?,?,?,?,?)",
                (book_id, kind, scraper, message, json.dumps(meta) if meta else None),
            )
            return int(cur.lastrowid)

    def recent_for_book(self, book_id: int, *, limit: int = 100) -> list[EventRow]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE book_id = ? ORDER BY id DESC LIMIT ?",
                (book_id, limit),
            ).fetchall()
        return [EventRow.from_row(r) for r in rows]

    def recent_global(self, *, limit: int = 500) -> list[EventRow]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [EventRow.from_row(r) for r in rows]

    def prune(
        self,
        *,
        keep_rows: int = 50_000,
        keep_days: int = 90,
    ) -> int:
        """Delete events older than `keep_days` days AND keep only the most
        recent `keep_rows` rows overall. Returns the number of rows deleted.

        Whichever cuts more wins — this protects both against "slow drip"
        retention (90 days of events from 1 active book = nothing) AND
        "burst growth" (a hundred re-queues in one day filling the table
        without crossing the day threshold).
        """
        deleted = 0
        with connect(self.db_path) as conn:
            # By age
            r = conn.execute(
                f"DELETE FROM events WHERE ts < datetime('now', '-{int(keep_days)} days')"
            )
            deleted += r.rowcount or 0
            # By total count
            total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            if total > keep_rows:
                excess = total - keep_rows
                r2 = conn.execute(
                    "DELETE FROM events WHERE id IN ("
                    "  SELECT id FROM events ORDER BY id ASC LIMIT ?"
                    ")",
                    (excess,),
                )
                deleted += r2.rowcount or 0
            conn.execute("VACUUM")
        return deleted
