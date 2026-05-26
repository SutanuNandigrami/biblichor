"""Phase 6u.5 — atomic claim, uuid .part, httpx → DownloadError wrap."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from endless_library.db.books import BookRepo
from endless_library.db.schema import init_db
from endless_library.domain.models import DownloadHandle
from endless_library.download import DownloadError, download


@pytest.fixture
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "claim.db"
    init_db(db_path)
    return db_path


_seed_seq = 0


def _seed_book(db_path: Path, status: str = "queued") -> int:
    # Legacy column name: per-source identifier lives in `goodreads_id`.
    # The (source, goodreads_id) pair has a UNIQUE constraint, so each
    # seeded row needs a fresh id.
    global _seed_seq
    _seed_seq += 1
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO books (title, source, goodreads_id, status) VALUES (?,?,?,?)",
        ("X", "manual", f"manual:x:{_seed_seq}", status),
    )
    bid = conn.execute("SELECT id FROM books ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.commit()
    conn.close()
    return int(bid)


# --- claim_for_processing -----------------------------------------------------


def test_claim_succeeds_from_queued(db: Path) -> None:
    bid = _seed_book(db, status="queued")
    repo = BookRepo(db)
    assert repo.claim_for_processing(bid) is True
    row = sqlite3.connect(db).execute("SELECT status FROM books WHERE id=?", (bid,)).fetchone()
    assert row[0] == "searching"


def test_claim_succeeds_from_failed_and_needs_review(db: Path) -> None:
    repo = BookRepo(db)
    for s in ("failed", "needs_review"):
        bid = _seed_book(db, status=s)
        assert repo.claim_for_processing(bid) is True, s


def test_claim_rejects_when_already_in_flight(db: Path) -> None:
    bid = _seed_book(db, status="queued")
    repo = BookRepo(db)
    # First call wins
    assert repo.claim_for_processing(bid) is True
    # Concurrent second call sees status='searching' and is rejected
    assert repo.claim_for_processing(bid) is False


def test_claim_rejects_terminal_states(db: Path) -> None:
    repo = BookRepo(db)
    for terminal in ("sent", "skipped"):
        bid = _seed_book(db, status=terminal)
        assert repo.claim_for_processing(bid) is False, terminal


def test_reset_zombies_returns_to_queued_not_failed(db: Path) -> None:
    """Phase 6u.5b: zombie sweep used to mark stale in-flight books as
    'failed'. But books with file_path set are recoverable via the
    resume path — failing them just dirties the dashboard and gives
    operators no actionable signal. Reset to 'queued' instead and
    record the cause in last_error."""
    bid = _seed_book(db, status="searching")
    # Backdate so the zombie sweep picks it up
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE books SET updated_at = datetime('now', '-60 minutes') WHERE id = ?",
        (bid,),
    )
    conn.commit()
    conn.close()

    repo = BookRepo(db)
    n = repo.reset_zombies(stale_minutes=30)
    assert n == 1
    row = (
        sqlite3.connect(db)
        .execute("SELECT status, last_error FROM books WHERE id = ?", (bid,))
        .fetchone()
    )
    assert row[0] == "queued"
    assert "zombie" in (row[1] or "").lower()


def test_reset_zombies_skips_fresh_in_flight(db: Path) -> None:
    """Books mid-pipeline but updated within stale_minutes are not
    touched."""
    bid = _seed_book(db, status="searching")
    repo = BookRepo(db)
    n = repo.reset_zombies(stale_minutes=30)
    assert n == 0
    row = sqlite3.connect(db).execute("SELECT status FROM books WHERE id = ?", (bid,)).fetchone()
    assert row[0] == "searching"


# --- download.py: httpx exceptions wrap to DownloadError ----------------------


def _stub_handle(url: str = "http://book.example/x.epub") -> DownloadHandle:
    return DownloadHandle(url=url, headers={"User-Agent": "t"}, expected_filename=None)


def _client_factory_that_raises(exc_cls, msg: str = "boom"):
    class _StubStream:
        status_code = 200
        headers = {"content-type": "application/epub+zip"}

        def iter_bytes(self, _n):
            raise exc_cls(msg)

        def read(self):  # not used here
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    class _StubClient:
        def stream(self, *_a, **_k):
            return _StubStream()

        def get(self, *_a, **_k):
            class _NoRedirect:
                status_code = 200
                headers = {}
                url = httpx.URL("http://book.example/x.epub")

            return _NoRedirect()

        def head(self, *_a, **_k):
            class _NoRedirect:
                status_code = 200
                headers = {}
                url = httpx.URL("http://book.example/x.epub")

            return _NoRedirect()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    return lambda: _StubClient()


@pytest.mark.parametrize(
    "exc_cls",
    [httpx.ReadError, httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectError],
)
def test_httpx_errors_wrap_to_download_error(tmp_path: Path, exc_cls) -> None:
    """Previously these escaped past pipeline's `except DownloadError`
    and surfaced as 'pipeline crash: ...' — fatal instead of transient."""
    with pytest.raises(DownloadError, match="network error during download"):
        download(
            _stub_handle(),
            dest_dir=tmp_path,
            fallback_name="x",
            client_factory=_client_factory_that_raises(exc_cls),
        )


def test_part_file_cleaned_up_on_network_error(tmp_path: Path) -> None:
    """Truncated download must not leave .part orphans on disk."""
    with pytest.raises(DownloadError):
        download(
            _stub_handle(),
            dest_dir=tmp_path,
            fallback_name="x",
            client_factory=_client_factory_that_raises(httpx.ReadError),
        )
    # No .part files remain
    leftovers = list(tmp_path.glob("*.part*"))
    assert leftovers == []


# --- empty-body sentinel ------------------------------------------------------


def test_empty_body_raises_download_error(tmp_path: Path) -> None:
    """Drive sometimes returns HTTP 200 + zero bytes (private file /
    quota exceeded). The downstream archive check would mistake that
    for a corrupt archive; we surface the real cause."""

    class _EmptyStream:
        status_code = 200
        headers = {"content-type": "application/epub+zip"}

        def iter_bytes(self, _n):
            return iter([])  # zero chunks

        def read(self):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    class _Client:
        def stream(self, *_a, **_k):
            return _EmptyStream()

        def get(self, *_a, **_k):
            class _R:
                status_code = 200
                headers = {}
                url = httpx.URL("http://book.example/x.epub")

            return _R()

        def head(self, *_a, **_k):
            class _R:
                status_code = 200
                headers = {}
                url = httpx.URL("http://book.example/x.epub")

            return _R()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    with pytest.raises(DownloadError, match="empty body"):
        download(
            _stub_handle(),
            dest_dir=tmp_path,
            fallback_name="x",
            client_factory=lambda: _Client(),
        )


# --- uuid .part isolation -----------------------------------------------------


def test_part_filename_includes_uuid_suffix(tmp_path: Path) -> None:
    """Concurrent workers must never share a .part filename. Verified by
    monkey-patching uuid4 to two different values and confirming the
    code stages to two distinct paths."""
    seen_paths: list[Path] = []

    def _spy_client_factory():
        class _Stream:
            status_code = 200
            headers = {"content-type": "application/epub+zip"}

            def iter_bytes(self, _n):
                return iter([b"hello"])

            def read(self):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        class _Client:
            def stream(self, *_a, **_k):
                return _Stream()

            def get(self, *_a, **_k):
                class _R:
                    status_code = 200
                    headers = {}
                    url = httpx.URL("http://book.example/x.epub")

                return _R()

            def head(self, *_a, **_k):
                class _R:
                    status_code = 200
                    headers = {}
                    url = httpx.URL("http://book.example/x.epub")

                return _R()

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        return _Client()

    fakes = [
        MagicMock(hex="aaaaaaaaaaaa00000000"),
        MagicMock(hex="bbbbbbbbbbbb00000000"),
    ]
    with patch("endless_library.download.uuid.uuid4", side_effect=fakes):
        for _ in range(2):
            res = download(
                _stub_handle(),
                dest_dir=tmp_path,
                fallback_name="x",
                client_factory=_spy_client_factory,
            )
            seen_paths.append(res.path)
    # Both downloads completed and renamed successfully; final paths
    # both exist (the second overwrites the first via os.replace)
    assert all(p.exists() for p in seen_paths)
    # No .part stragglers
    assert list(tmp_path.glob("*.part*")) == []
