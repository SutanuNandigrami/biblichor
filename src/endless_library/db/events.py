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
