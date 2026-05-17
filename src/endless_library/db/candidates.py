from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .schema import connect


@dataclass(frozen=True, slots=True)
class CandidateRow:
    id: int
    book_id: int
    provider: str
    md5: str | None
    title: str | None
    author: str | None
    language: str | None
    format: str | None
    filesize_bytes: int | None
    year: int | None
    publisher: str | None
    edition_hints: str | None
    score: float | None
    detail_url: str
    raw_json: str | None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> CandidateRow:
        return cls(
            id=r["id"],
            book_id=r["book_id"],
            provider=r["provider"],
            md5=r["md5"],
            title=r["title"],
            author=r["author"],
            language=r["language"],
            format=r["format"],
            filesize_bytes=r["filesize_bytes"],
            year=r["year"],
            publisher=r["publisher"],
            edition_hints=r["edition_hints"],
            score=r["score"],
            detail_url=r["detail_url"],
            raw_json=r["raw_json"],
        )


class CandidateRepo:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def insert(
        self,
        *,
        book_id: int,
        provider: str,
        md5: str | None,
        title: str | None,
        author: str | None,
        language: str | None,
        format: str | None,
        filesize_bytes: int | None,
        year: int | None,
        publisher: str | None,
        edition_hints: str | None,
        score: float,
        detail_url: str,
        raw_json: str,
    ) -> int:
        with connect(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO candidates (book_id, provider, md5, title, author, language,
                                        format, filesize_bytes, year, publisher,
                                        edition_hints, score, detail_url, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    book_id,
                    provider,
                    md5,
                    title,
                    author,
                    language,
                    format,
                    filesize_bytes,
                    year,
                    publisher,
                    edition_hints,
                    score,
                    detail_url,
                    raw_json,
                ),
            )
            return int(cur.lastrowid)

    def top_for_book(self, book_id: int, *, limit: int = 5) -> list[CandidateRow]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM candidates WHERE book_id = ? ORDER BY score DESC, id ASC LIMIT ?",
                (book_id, limit),
            ).fetchall()
        return [CandidateRow.from_row(r) for r in rows]

    def clear_for_book(self, book_id: int) -> None:
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM candidates WHERE book_id = ?", (book_id,))
