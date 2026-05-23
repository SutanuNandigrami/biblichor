"""Phase STK 12: end-to-end STK integration smoke test.

Mocks only the HTTP layer (intercepts requests to amazon.com and
stkservice.amazon.com). Runs the OAuth -> register-device -> send flow
through the real KindleStkService + kindle_router stack.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from endless_library.db.schema import init_db, connect


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "test.db"
    init_db(p)
    return p


@pytest.fixture
def svc():
    class _Svc:
        def __init__(self): self._s: dict[str, str] = {}
        def get_secret_value(self, k): return self._s.get(k)
        def set_secret_value(self, k, v): self._s[k] = v
        def set_secret_values(self, kv): self._s.update(kv)
        def delete_secret_value(self, k): self._s.pop(k, None)
    return _Svc()


def test_router_smoke_records_send_stk_event_via_router(db, svc, tmp_path, monkeypatch):
    """Path: router with fake vendored client + real event-log writes.
    This is the most useful smoke -- it asserts the wiring from router
    down through KindleStkService -> vendored client -> event log all
    holds together."""
    from tests._stkclient_stub import FakeVendoredClient
    fake = FakeVendoredClient()
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake,
    )
    # Configure STK
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    svc.set_secret_value("kindle_stk.default_destination_sn", "G0WEB1")
    svc.set_secret_value("kindle_stk.default_destination_name", "Kindle for Web")

    # Insert a book row so events.book_id has somewhere to point
    with connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO books (title, author, status, source) VALUES ('Test', 'A', 'queued', 'manual')"
        )
        book_id = cur.lastrowid

    cfg = SimpleNamespace(
        stk=SimpleNamespace(daily_cap=500, max_attempts=3,
                            backoff_initial_sec=0.0, backoff_factor=1.0),
        smtp=SimpleNamespace(daily_cap=80),
    )
    book = SimpleNamespace(id=book_id, title="Test", author="A")
    f = tmp_path / "book.epub"
    f.write_bytes(b"FAKE")

    from endless_library.kindle_router import deliver, DeliveryMethod
    result = deliver(file_path=f, book=book, cfg=cfg, db_path=db, svc=svc)
    assert result.ok is True
    assert result.method == DeliveryMethod.STK
    assert fake.send_calls, "vendored client send_file was not invoked"

    with connect(db) as conn:
        events = [r["kind"] for r in conn.execute(
            "SELECT kind FROM events WHERE book_id = ?", (book_id,)
        )]
    assert "send-stk" in events
