from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

EXPECTED_TABLES = (
    "books",
    "candidates",
    "events",
    "source_accounts",
    "bench_runs",
    "mirrors",
    "bench_jobs",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS books (
  id              INTEGER PRIMARY KEY,
  title           TEXT    NOT NULL,
  author          TEXT,
  isbn13          TEXT,
  goodreads_id    TEXT,
  hardcover_id    TEXT,
  source          TEXT    NOT NULL,
  source_added_at TEXT,
  status          TEXT    NOT NULL DEFAULT 'queued',
  format          TEXT,
  file_path       TEXT,
  file_size       INTEGER,
  md5             TEXT,
  picked_candidate_id INTEGER,
  attempts        INTEGER NOT NULL DEFAULT 0,
  last_error      TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  searched_at     TEXT,
  downloaded_at   TEXT,
  converted_at    TEXT,
  sent_at         TEXT,
  series          TEXT,
  tags            TEXT,
  UNIQUE(source, goodreads_id),
  UNIQUE(source, hardcover_id)
);

CREATE TABLE IF NOT EXISTS candidates (
  id             INTEGER PRIMARY KEY,
  book_id        INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  provider       TEXT    NOT NULL,
  md5            TEXT,
  title          TEXT,
  author         TEXT,
  language       TEXT,
  format         TEXT,
  filesize_bytes INTEGER,
  year           INTEGER,
  publisher      TEXT,
  edition_hints  TEXT,
  score          REAL,
  detail_url     TEXT NOT NULL,
  raw_json       TEXT,
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_candidates_book ON candidates(book_id);

CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY,
  book_id    INTEGER REFERENCES books(id) ON DELETE CASCADE,
  ts         TEXT NOT NULL DEFAULT (datetime('now')),
  kind       TEXT NOT NULL,
  scraper    TEXT,
  message    TEXT NOT NULL,
  meta_json  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_book_ts ON events(book_id, ts DESC);

CREATE TABLE IF NOT EXISTS source_accounts (
  id          INTEGER PRIMARY KEY,
  source      TEXT NOT NULL,
  identifier  TEXT NOT NULL,
  token       TEXT,
  enabled     INTEGER NOT NULL DEFAULT 1,
  poll_interval_minutes INTEGER NOT NULL DEFAULT 60,
  last_polled_at TEXT,
  UNIQUE(source, identifier)
);

CREATE TABLE IF NOT EXISTS mirrors (
  id              INTEGER PRIMARY KEY,
  kind            TEXT NOT NULL,           -- 'annas' | 'welib' | 'libgen'
  url             TEXT NOT NULL UNIQUE,
  label           TEXT,
  enabled         INTEGER NOT NULL DEFAULT 1,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_probed_at  TEXT,
  last_ok_at      TEXT,
  last_status     INTEGER,
  last_latency_ms INTEGER,
  last_error      TEXT
);
CREATE INDEX IF NOT EXISTS idx_mirrors_kind ON mirrors(kind, enabled);

CREATE TABLE IF NOT EXISTS bench_runs (
  id         INTEGER PRIMARY KEY,
  ts         TEXT NOT NULL DEFAULT (datetime('now')),
  scraper    TEXT NOT NULL,
  query      TEXT NOT NULL,
  success    INTEGER NOT NULL,
  duration_ms INTEGER,
  http_code  INTEGER,
  notes      TEXT
);

CREATE TABLE IF NOT EXISTS ipfs_gateways (
    url             TEXT PRIMARY KEY,
    origin_isolation INTEGER DEFAULT 0,
    last_ok          INTEGER,
    last_check       INTEGER
);

CREATE TABLE IF NOT EXISTS metadata_cache (
    key         TEXT PRIMARY KEY,
    payload     BLOB NOT NULL,
    fetched_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS bench_jobs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  mode            TEXT NOT NULL,
  status          TEXT NOT NULL CHECK(status IN ('running','done','cancelled','failed')),
  progress_done   INTEGER NOT NULL DEFAULT 0,
  progress_total  INTEGER NOT NULL,
  summary_json    TEXT
);
"""


@contextmanager
def connect(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Path | str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _migrate(conn)
        # Partial UNIQUE index on isbn13 — defense in depth alongside the
        # BEGIN IMMEDIATE wrap of BookRepo.upsert. If two concurrent poll
        # jobs race past the SELECT, the second INSERT trips this index and
        # the caller's except path looks up the row that won.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_books_isbn13 "
            "ON books(isbn13) WHERE isbn13 IS NOT NULL"
        )


def _migrate(conn) -> None:
    """Best-effort schema migrations for older DBs. ALTER TABLE ADD COLUMN
    is idempotent only when wrapped in try/except (SQLite raises if exists)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(books)")}
    for new_col, ddl in (
        ("searched_at", "ALTER TABLE books ADD COLUMN searched_at TEXT"),
        ("downloaded_at", "ALTER TABLE books ADD COLUMN downloaded_at TEXT"),
        ("converted_at", "ALTER TABLE books ADD COLUMN converted_at TEXT"),
        # Phase 6s.1: PD pre-chain hook columns
        ("pub_year", "ALTER TABLE books ADD COLUMN pub_year INTEGER"),
        ("is_public_domain", "ALTER TABLE books ADD COLUMN is_public_domain INTEGER"),
    ):
        if new_col not in cols:
            conn.execute(ddl)
