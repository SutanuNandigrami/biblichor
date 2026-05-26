"""Unit tests for STK multi-file batching with 200 MB budget.

Tests:
  - test_pack_files_under_200mb -- greedy packer respects budget
  - test_pack_skips_single_oversized_file -- single >200 MB -> skip + oversized list
  - test_pack_respects_max_batch_files -- 30 small files split into 25+5
  - test_send_files_records_one_event_per_book -- via deliver_batch audit log
  - test_send_files_translates_403_to_auth_expired -- exception typing preserved
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from endless_library.kindle_router import BookFile, _pack_batches
from endless_library.kindle_stk.exceptions import (
    KindleStkBatchOverflow,
)
from endless_library.kindle_stk.service import FileEntry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def svc():
    class _Svc:
        def __init__(self):
            self._s: dict[str, str] = {}

        def get_secret_value(self, k):
            return self._s.get(k)

        def set_secret_value(self, k, v):
            self._s[k] = v

        def set_secret_values(self, kv):
            self._s.update(kv)

        def delete_secret_value(self, k):
            self._s.pop(k, None)

    return _Svc()


@pytest.fixture
def configured_svc(svc):
    """A svc with STK credentials and a default device pre-configured."""
    svc.set_secret_value("kindle_stk.device_cert.pem", "FAKEPEM")
    svc.set_secret_value("kindle_stk.adp_token", "FAKEADP")
    svc.set_secret_value("kindle_stk.default_destination_sn", "G0WEB1")
    svc.set_secret_value("kindle_stk.default_destination_name", "Kindle for Web")
    return svc


def _make_epub(tmp_path: Path, name: str, size_bytes: int) -> Path:
    p = tmp_path / name
    p.write_bytes(b"x" * size_bytes)
    return p


# ---------------------------------------------------------------------------
# Greedy packer tests
# ---------------------------------------------------------------------------


def test_pack_files_under_200mb(tmp_path):
    """Greedy packer keeps total <= 200 MB per batch."""
    MB = 1024 * 1024
    files = [
        BookFile(
            book_id=i,
            file_path=_make_epub(tmp_path, f"b{i}.epub", 50 * MB),
            title=f"Book {i}",
            author="A",
        )
        for i in range(5)
    ]
    # 5 x 50 MB = 250 MB total; should produce 2 batches: [4 files, 1 file]
    batches, oversized = _pack_batches(files, max_bytes=200 * MB, max_files=25)
    assert not oversized
    assert len(batches) == 2
    for batch in batches:
        total = sum(bf.file_path.stat().st_size for bf in batch)
        assert total <= 200 * MB


def test_pack_skips_single_oversized_file(tmp_path):
    """A single file > 200 MB goes to the oversized list, not any batch."""
    MB = 1024 * 1024
    big = _make_epub(tmp_path, "big.epub", 201 * MB)
    small = _make_epub(tmp_path, "small.epub", 10 * MB)
    files = [
        BookFile(book_id=1, file_path=big, title="Big", author="A"),
        BookFile(book_id=2, file_path=small, title="Small", author="A"),
    ]
    batches, oversized = _pack_batches(files, max_bytes=200 * MB, max_files=25)
    assert len(oversized) == 1
    assert oversized[0].book_id == 1
    assert len(batches) == 1
    assert batches[0][0].book_id == 2


def test_pack_respects_max_batch_files(tmp_path):
    """30 small files with max_batch_files=25 -> two batches: 25 + 5."""
    MB = 1024 * 1024
    files = [
        BookFile(
            book_id=i,
            file_path=_make_epub(tmp_path, f"b{i}.epub", 1 * MB),
            title=f"Book {i}",
            author="A",
        )
        for i in range(30)
    ]
    batches, oversized = _pack_batches(files, max_bytes=200 * MB, max_files=25)
    assert not oversized
    assert len(batches) == 2
    assert len(batches[0]) == 25
    assert len(batches[1]) == 5


# ---------------------------------------------------------------------------
# KindleStkService.send_files tests
# ---------------------------------------------------------------------------


def test_send_files_records_one_event_per_book(tmp_path, configured_svc, monkeypatch):
    """deliver_batch writes one send-stk event per book (via router -> service)."""
    from endless_library.db.schema import connect, init_db
    from endless_library.kindle_router import BookFile, deliver_batch
    from tests._stkclient_stub import FakeVendoredClient

    fake = FakeVendoredClient()
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake,
    )

    db = tmp_path / "test.db"
    init_db(db)

    # Insert 3 book rows
    book_ids = []
    with connect(db) as conn:
        for i in range(3):
            cur = conn.execute(
                "INSERT INTO books (title, author, status, source) "
                "VALUES (?, ?, 'queued', 'manual')",
                (f"Book {i}", f"Author {i}"),
            )
            book_ids.append(cur.lastrowid)

    files = [
        BookFile(
            book_id=book_ids[i],
            file_path=_make_epub(tmp_path, f"b{i}.epub", 1024),
            title=f"Book {i}",
            author=f"Author {i}",
        )
        for i in range(3)
    ]

    cfg = SimpleNamespace(
        stk=SimpleNamespace(
            daily_cap=None,
            max_batch_files=25,
            max_batch_bytes=200 * 1024 * 1024,
            min_send_interval_sec=0.0,
        ),
        smtp=SimpleNamespace(daily_cap=80),
    )

    results = deliver_batch(files=files, cfg=cfg, db_path=db, svc=configured_svc)

    assert all(r.ok for r in results), f"Some failed: {[r.error for r in results]}"
    assert len(fake.send_calls) == 3, "FakeVendoredClient should have been called 3 times"

    with connect(db) as conn:
        events = [row["kind"] for row in conn.execute("SELECT kind FROM events ORDER BY id")]
    # One send-stk per book + one send-stk-batch for the batch
    assert events.count("send-stk") == 3
    assert events.count("send-stk-batch") == 1


def test_send_files_translates_403_to_auth_expired(tmp_path, configured_svc, monkeypatch):
    """A 403 HTTP error from stkclient is translated to KindleStkAuthExpired."""
    from endless_library.kindle_stk import KindleStkAuthExpired
    from endless_library.kindle_stk._vendored import _api
    from endless_library.kindle_stk.service import KindleStkService

    # Simulate 403 via APIError (the path used in the vendored layer)
    err_403 = _api.APIError(
        "HTTP Error 403: Forbidden", b'{"Message": "Failed to validate DeviceInfoToken."}'
    )

    class Fake403Client:
        def get_owned_devices(self):
            from tests._stkclient_stub import FakeDevice

            return [
                FakeDevice(
                    device_serial_number="G0WEB1",
                    device_type="FionaWebApp",
                    device_name="Kindle for Web",
                )
            ]

        def send_file(self, *a, **kw):
            raise err_403

    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: Fake403Client(),
    )

    stk_svc = KindleStkService(configured_svc)
    entry = FileEntry(
        path=_make_epub(tmp_path, "book.epub", 1024),
        title="Test",
        author="Author",
        format="EPUB",
    )
    with pytest.raises(KindleStkAuthExpired):
        stk_svc.send_files([entry])


def test_send_files_raises_batch_overflow_for_oversized(tmp_path, configured_svc, monkeypatch):
    """A file > 200 MB passed to send_files raises KindleStkBatchOverflow."""
    from endless_library.kindle_stk.service import KindleStkService
    from tests._stkclient_stub import FakeVendoredClient

    fake = FakeVendoredClient()
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake,
    )

    stk_svc = KindleStkService(configured_svc)
    big = _make_epub(tmp_path, "big.epub", 201 * 1024 * 1024)
    entry = FileEntry(path=big, title="Big", author="A", format="EPUB")

    with pytest.raises(KindleStkBatchOverflow) as exc_info:
        stk_svc.send_files([entry])
    assert exc_info.value.size_bytes > 200 * 1024 * 1024


# ---------------------------------------------------------------------------
# Per-file throttle tests (Fix 1: STK batch path violates the throttle)
# ---------------------------------------------------------------------------


def test_send_files_sleeps_per_file_interval(tmp_path, configured_svc, monkeypatch):
    """send_files() sleeps per_file_interval_sec between files (not before first,
    not after last). A 3-file batch must call time.sleep exactly twice."""
    import endless_library.kindle_stk.service as stk_svc_module
    from endless_library.kindle_stk.service import KindleStkService
    from tests._stkclient_stub import FakeVendoredClient

    fake = FakeVendoredClient()
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake,
    )

    sleep_calls: list[float] = []
    monkeypatch.setattr(stk_svc_module.time, "sleep", lambda s: sleep_calls.append(s))

    stk_svc = KindleStkService(configured_svc, per_file_interval_sec=7.0)
    files = [
        FileEntry(
            path=_make_epub(tmp_path, f"book{i}.epub", 1024),
            title=f"Book {i}",
            author="Author",
            format="EPUB",
        )
        for i in range(3)
    ]

    stk_svc.send_files(files)

    # Exactly 2 sleeps: between file 0-1 and 1-2 (NOT before 0, NOT after 2).
    assert sleep_calls == [7.0, 7.0], f"Expected [7.0, 7.0], got {sleep_calls!r}"
    assert len(fake.send_calls) == 3
