"""Integration smoke test: pipeline batch STK delivery.

FakeVendoredClient accepts send_file calls (one per book). We verify:
  - A queue of 5 books -> exactly 1 deliver_batch call -> 5 individual
    send_file calls on the vendored client (batched in one signed session).
  - 5 'send-stk' events + 1 'send-stk-batch' event in the audit log.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from endless_library.db.schema import connect, init_db


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "test.db"
    init_db(p)
    return p


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


def test_pipeline_batch_smoke(db, svc, tmp_path, monkeypatch):
    """Queue of 5 books -> 1 batch call -> 5 send events + 1 batch event."""
    from tests._stkclient_stub import FakeVendoredClient

    fake = FakeVendoredClient()
    batch_call_count = [0]

    # Patch FakeVendoredClient into the vendored layer
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake,
    )

    # Patch _kindle_deliver_batch to count calls (wraps real deliver_batch)
    import endless_library.kindle_router as _router_mod
    import endless_library.pipeline as _pipeline_mod

    real_deliver_batch = _router_mod.deliver_batch

    def counting_deliver_batch(**kw):
        batch_call_count[0] += 1
        return real_deliver_batch(**kw)

    monkeypatch.setattr(_pipeline_mod, "_kindle_deliver_batch", counting_deliver_batch)

    # Configure STK
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    svc.set_secret_value("kindle_stk.default_destination_sn", "G0WEB1")
    svc.set_secret_value("kindle_stk.default_destination_name", "Kindle for Web")

    # Insert 5 books with already-downloaded files (resume path)
    book_ids = []
    with connect(db) as conn:
        for i in range(5):
            f = tmp_path / f"book{i}.epub"
            f.write_bytes(b"FAKE EPUB CONTENT " * 10)
            cur = conn.execute(
                """INSERT INTO books
                   (title, author, status, source, file_path, downloaded_at, format)
                   VALUES (?, ?, 'queued', 'manual', ?, datetime('now'), 'epub')""",
                (f"Book {i}", f"Author {i}", str(f)),
            )
            book_ids.append(cur.lastrowid)

    cfg = SimpleNamespace(
        general=SimpleNamespace(
            max_attempts=5,
            zombie_stale_minutes=30,
            parallel_books=1,
        ),
        stk=SimpleNamespace(
            daily_cap=None,
            max_attempts=3,
            backoff_initial_sec=0.0,
            backoff_factor=1.0,
            min_send_interval_sec=0.0,
            max_batch_files=25,
            max_batch_bytes=200 * 1024 * 1024,
        ),
        smtp=SimpleNamespace(
            daily_cap=80,
            max_attachment_mb=24,
        ),
        calibre=SimpleNamespace(enabled=False),
        bookorbit=SimpleNamespace(enabled=False, library_root=""),
        scoring=SimpleNamespace(deliverable_max_bytes=None),
        pushover=SimpleNamespace(enabled=False),
    )

    # Build minimal PipelineDeps
    from endless_library.db.books import BookRepo
    from endless_library.db.events import EventRepo

    class _FakeNotifier:
        def book_sent(self, *a, **kw):
            pass

    class _Deps:
        pass

    deps = _Deps()
    deps.db_path = db
    deps.cfg = cfg
    deps.books = BookRepo(db)
    deps.events = EventRepo(db)
    deps.bookorbit_service = svc
    deps.notifier = _FakeNotifier()

    from endless_library.pipeline import process_queue

    tally = process_queue(deps)

    assert tally["sent"] == 5, f"Expected 5 sent, got: {tally}"
    assert tally["failed"] == 0, f"Unexpected failures: {tally}"

    # Verify batch call happened (exactly 1, since all 5 fit in 1 batch of 25)
    assert batch_call_count[0] == 1, f"Expected 1 deliver_batch call, got {batch_call_count[0]}"

    # Verify audit log
    with connect(db) as conn:
        event_kinds = [row["kind"] for row in conn.execute("SELECT kind FROM events ORDER BY id")]
    assert event_kinds.count("send-stk") == 5
    assert event_kinds.count("send-stk-batch") == 1

    # Verify vendored client was called 5 times
    assert len(fake.send_calls) == 5, (
        f"Expected 5 vendor send_file calls, got {len(fake.send_calls)}"
    )
