from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .schema import connect

_IN_FLIGHT = ("searching", "downloading", "converting", "sending")


@dataclass(frozen=True, slots=True)
class BookRow:
    id: int
    title: str
    author: str | None
    isbn13: str | None
    goodreads_id: str | None
    hardcover_id: str | None
    source: str
    status: str
    format: str | None
    file_path: str | None
    md5: str | None
    picked_candidate_id: int | None
    attempts: int
    last_error: str | None
    created_at: str
    updated_at: str
    searched_at: str | None
    downloaded_at: str | None
    converted_at: str | None
    sent_at: str | None
    series: str | None
    tags: str | None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> BookRow:
        return cls(
            id=r["id"],
            title=r["title"],
            author=r["author"],
            isbn13=r["isbn13"],
            goodreads_id=r["goodreads_id"],
            hardcover_id=r["hardcover_id"],
            source=r["source"],
            status=r["status"],
            format=r["format"],
            file_path=r["file_path"],
            md5=r["md5"],
            picked_candidate_id=r["picked_candidate_id"],
            attempts=r["attempts"],
            last_error=r["last_error"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            searched_at=r["searched_at"],
            downloaded_at=r["downloaded_at"],
            converted_at=r["converted_at"],
            sent_at=r["sent_at"],
            series=dict(r).get("series"),
            tags=dict(r).get("tags"),
        )


class BookRepo:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def _connect(self):
        return connect(self.db_path)

    def upsert(
        self,
        *,
        title: str,
        author: str | None,
        isbn13: str | None,
        source: str,
        source_id: str,
        source_added_at: str | None = None,
    ) -> int:
        # Column where we slot the source_id. Manual entries reuse goodreads_id column;
        # uniqueness still holds via UNIQUE(source, goodreads_id).
        col = "hardcover_id" if source == "hardcover" else "goodreads_id"
        with self._connect() as conn:
            # Race-safe upsert: take an IMMEDIATE write lock so concurrent
            # poll jobs can't both miss the dedup row and double-insert.
            # The partial UNIQUE index on isbn13 catches anything that
            # slips past (e.g. our own retry on transient errors).
            conn.execute("BEGIN IMMEDIATE")
            try:
                # 1. ISBN-level dedup (cross-source)
                if isbn13:
                    row = conn.execute(
                        "SELECT id FROM books WHERE isbn13 = ?", (isbn13,)
                    ).fetchone()
                    if row:
                        conn.execute("COMMIT")
                        return int(row["id"])
                # 2. Source-specific dedup
                existing = conn.execute(
                    f"SELECT id FROM books WHERE source = ? AND {col} = ?",
                    (source, source_id),
                ).fetchone()
                if existing:
                    conn.execute("COMMIT")
                    return int(existing["id"])
                cur = conn.execute(
                    f"""
                    INSERT INTO books (title, author, isbn13, {col}, source, source_added_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (title, author, isbn13, source_id, source, source_added_at),
                )
                new_id = int(cur.lastrowid)
                conn.execute("COMMIT")
                return new_id
            except Exception:
                conn.execute("ROLLBACK")
                # If we lost the race, look up the row that won
                if isbn13:
                    row = conn.execute(
                        "SELECT id FROM books WHERE isbn13 = ?", (isbn13,)
                    ).fetchone()
                    if row:
                        return int(row["id"])
                raise

    def get(self, book_id: int) -> BookRow | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return BookRow.from_row(row) if row else None

    def find_by_isbn(self, isbn13: str) -> BookRow | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM books WHERE isbn13 = ?", (isbn13,)).fetchone()
        return BookRow.from_row(row) if row else None

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS c FROM books").fetchone()["c"])

    def set_status(
        self,
        book_id: int,
        status: str,
        *,
        error: str | None = None,
        format: str | None = None,
        file_path: str | None = None,
        md5: str | None = None,
        file_size: int | None = None,
    ) -> None:
        fields = ["status = ?", "updated_at = datetime('now')"]
        params: list[object] = [status]
        if error is not None:
            fields.append("last_error = ?")
            params.append(error)
        if format is not None:
            fields.append("format = ?")
            params.append(format)
        if file_path is not None:
            fields.append("file_path = ?")
            params.append(file_path)
        if md5 is not None:
            fields.append("md5 = ?")
            params.append(md5)
        if file_size is not None:
            fields.append("file_size = ?")
            params.append(file_size)
        # Stage-completion timestamps fire on the canonical 'success' transitions
        if status == "downloading":
            fields.append("downloaded_at = datetime('now')")
        elif status == "sending":
            # The convert step (if any) just completed before we switch to sending.
            # We track converted_at separately via mark_stage.
            pass
        elif status == "sent":
            fields.append("sent_at = datetime('now')")
        params.append(book_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE books SET {', '.join(fields)} WHERE id = ?", params)

    def mark_stage(self, book_id: int, stage: str) -> None:
        """Set a stage-completion timestamp. stage ∈ {searched, downloaded, converted, sent}."""
        col = {
            "searched": "searched_at",
            "downloaded": "downloaded_at",
            "converted": "converted_at",
            "sent": "sent_at",
        }[stage]
        with self._connect() as conn:
            conn.execute(
                f"UPDATE books SET {col} = datetime('now'), updated_at = datetime('now') WHERE id = ?",
                (book_id,),
            )

    def clear_stages_from(self, book_id: int, *, stage: str) -> None:
        """Reset stage-completion timestamps from `stage` onward. Used on retry
        when the user wants a true 'restart from scratch'."""
        cols_from = {
            "searched": ["searched_at", "downloaded_at", "converted_at", "sent_at"],
            "downloaded": ["downloaded_at", "converted_at", "sent_at"],
            "converted": ["converted_at", "sent_at"],
            "sent": ["sent_at"],
        }[stage]
        sets = ", ".join(f"{c} = NULL" for c in cols_from)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE books SET {sets}, updated_at = datetime('now') WHERE id = ?",
                (book_id,),
            )

    def set_tags(self, book_id: int, *, series: str | None = None, tags: str | None = None) -> None:
        """Update tags/series. None values are left unchanged."""
        sets = []
        params: list[object] = []
        if series is not None:
            sets.append("series = ?")
            params.append(series)
        if tags is not None:
            sets.append("tags = ?")
            params.append(tags)
        if not sets:
            return
        sets.append("updated_at = datetime('now')")
        params.append(book_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE books SET {', '.join(sets)} WHERE id = ?", params)

    def increment_attempts(self, book_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE books SET attempts = attempts + 1, updated_at = datetime('now') WHERE id = ?",
                (book_id,),
            )

    def pending(self, *, max_attempts: int) -> list[BookRow]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM books
                 WHERE status IN ('queued', 'failed')
                   AND attempts < ?
                 ORDER BY created_at ASC
                """,
                (max_attempts,),
            ).fetchall()
        return [BookRow.from_row(r) for r in rows]

    def reset_for_research(self, book_id: int) -> None:
        """Full reset for a re-search: clears every stage timestamp,
        on-disk file reference, picked candidate, attempts, and last_error.
        The actual file on disk is left untouched (the new search may
        re-pick the same md5 and a fresh download is wasted bandwidth).
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE books SET "
                "  status = 'queued', "
                "  attempts = 0, "
                "  last_error = NULL, "
                "  picked_candidate_id = NULL, "
                "  searched_at = NULL, "
                "  downloaded_at = NULL, "
                "  converted_at = NULL, "
                "  sent_at = NULL, "
                "  file_path = NULL, "
                "  md5 = NULL, "
                "  format = NULL, "
                "  file_size = NULL, "
                "  updated_at = datetime('now') "
                "WHERE id = ?",
                (book_id,),
            )

    def reset_zombies(self, *, stale_minutes: int) -> int:
        in_flight = ",".join(f"'{s}'" for s in _IN_FLIGHT)
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                UPDATE books
                   SET status = 'failed',
                       last_error = 'reset by zombie sweep',
                       updated_at = datetime('now')
                 WHERE status IN ({in_flight})
                   AND updated_at < datetime('now', ?)
                """,
                (f"-{stale_minutes} minutes",),
            )
            return int(cur.rowcount)
