from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .schema import connect


@dataclass(frozen=True, slots=True)
class SourceAccountRow:
    id: int
    source: str
    identifier: str
    token: str | None
    enabled: bool
    poll_interval_minutes: int
    last_polled_at: str | None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> SourceAccountRow:
        return cls(
            id=r["id"],
            source=r["source"],
            identifier=r["identifier"],
            token=r["token"],
            enabled=bool(r["enabled"]),
            poll_interval_minutes=r["poll_interval_minutes"],
            last_polled_at=r["last_polled_at"],
        )


class SourceAccountRepo:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def add(
        self,
        *,
        source: str,
        identifier: str,
        token: str | None,
        poll_interval_minutes: int = 60,
    ) -> int:
        with connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO source_accounts (source, identifier, token, poll_interval_minutes)
                   VALUES (?, ?, ?, ?)""",
                (source, identifier, token, poll_interval_minutes),
            )
            return int(cur.lastrowid)

    def get(self, account_id: int) -> SourceAccountRow | None:
        with connect(self.db_path) as conn:
            r = conn.execute("SELECT * FROM source_accounts WHERE id = ?", (account_id,)).fetchone()
        return SourceAccountRow.from_row(r) if r else None

    def list_all(self) -> list[SourceAccountRow]:
        with connect(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM source_accounts ORDER BY id").fetchall()
        return [SourceAccountRow.from_row(r) for r in rows]

    def list_enabled(self) -> list[SourceAccountRow]:
        return [r for r in self.list_all() if r.enabled]

    def set_enabled(self, account_id: int, enabled: bool) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE source_accounts SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, account_id),
            )

    def set_interval(self, account_id: int, minutes: int) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE source_accounts SET poll_interval_minutes = ? WHERE id = ?",
                (int(minutes), account_id),
            )

    def mark_polled(self, account_id: int) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE source_accounts SET last_polled_at = datetime('now') WHERE id = ?",
                (account_id,),
            )

    def delete(self, account_id: int) -> None:
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM source_accounts WHERE id = ?", (account_id,))
