"""Phase STK 8: kindle_router.deliver -- STK-first + SMTP-fallback."""
from __future__ import annotations
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from endless_library.db.schema import init_db, connect
from endless_library.kindle_stk import (
    KindleStkAuthExpired,
    KindleStkRateLimited,
    KindleStkUploadFailed,
)

@pytest.fixture
def db(tmp_path):
    p = tmp_path / "test.db"
    init_db(p)
    return p

@pytest.fixture
def cfg():
    return SimpleNamespace(
        stk=SimpleNamespace(daily_cap=500, max_attempts=3, backoff_initial_sec=0.0, backoff_factor=1.0),
        smtp=SimpleNamespace(daily_cap=80),
    )

@pytest.fixture
def book(db):
    with connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO books (title, author, status, source) VALUES (?, ?, ?, ?)",
            ("Test Book", "Test Author", "queued", "test"),
        )
        book_id = cur.lastrowid
    return SimpleNamespace(id=book_id, title="Test Book", author="Test Author")

@pytest.fixture
def file_path(tmp_path):
    f = tmp_path / "book.epub"
    f.write_bytes(b"FAKE EPUB BYTES")
    return f

@pytest.fixture
def svc():
    class _Svc:
        def __init__(self):
            self._secrets = {}
        def get_secret_value(self, key): return self._secrets.get(key)
        def set_secret_value(self, key, value): self._secrets[key] = value
        def set_secret_values(self, kv): self._secrets.update(kv)
        def delete_secret_value(self, key): self._secrets.pop(key, None)
    return _Svc()

def _configure_stk(svc):
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    svc.set_secret_value("kindle_stk.default_destination_sn", "G0WEB1")
    svc.set_secret_value("kindle_stk.default_destination_name", "Kindle for Web")

def test_router_picks_smtp_when_stk_not_configured(monkeypatch, db, cfg, book, file_path, svc):
    smtp_calls = []
    monkeypatch.setattr("endless_library.kindle_router._send_smtp", lambda *a, **kw: smtp_calls.append(1))
    from endless_library.kindle_router import deliver, DeliveryMethod
    result = deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)
    assert result.ok is True
    assert result.method == DeliveryMethod.SMTP
    assert smtp_calls

def test_router_picks_stk_when_configured(monkeypatch, db, cfg, book, file_path, svc):
    _configure_stk(svc)
    stk_calls = []
    def fake_stk_send(self, file, *, format, title, author):
        stk_calls.append((file, format, title, author))
        return {"transaction_id": "tx-1"}
    monkeypatch.setattr("endless_library.kindle_stk.service.KindleStkService.send_file", fake_stk_send)
    from endless_library.kindle_router import deliver, DeliveryMethod
    result = deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)
    assert result.ok is True
    assert result.method == DeliveryMethod.STK
    assert len(stk_calls) == 1

def test_router_falls_to_smtp_when_quota_exhausted(monkeypatch, db, cfg, book, file_path, svc):
    _configure_stk(svc)
    with connect(db) as conn:
        for _ in range(500):
            conn.execute("INSERT INTO events (kind, message, meta_json) VALUES (?, ?, ?)", ("send-stk", "seed", "{}"))
    stk_called = []
    monkeypatch.setattr("endless_library.kindle_stk.service.KindleStkService.send_file", lambda *a, **kw: stk_called.append(True))
    smtp_called = []
    monkeypatch.setattr("endless_library.kindle_router._send_smtp", lambda *a, **kw: smtp_called.append(True))
    from endless_library.kindle_router import deliver, DeliveryMethod
    result = deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)
    assert result.method == DeliveryMethod.SMTP
    assert not stk_called
    assert smtp_called

def test_router_retries_stk_3x_then_falls_to_smtp(monkeypatch, db, cfg, book, file_path, svc):
    _configure_stk(svc)
    attempts = []
    def fake_send(self, file, **kw):
        attempts.append(time.monotonic())
        raise KindleStkUploadFailed("transient")
    monkeypatch.setattr("endless_library.kindle_stk.service.KindleStkService.send_file", fake_send)
    smtp_called = []
    monkeypatch.setattr("endless_library.kindle_router._send_smtp", lambda *a, **kw: smtp_called.append(True))
    from endless_library.kindle_router import deliver, DeliveryMethod
    result = deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)
    assert len(attempts) == 3
    assert smtp_called
    assert result.method == DeliveryMethod.SMTP

def test_router_auth_expired_skips_retries(monkeypatch, db, cfg, book, file_path, svc):
    _configure_stk(svc)
    attempts = []
    def fake_send(self, file, **kw):
        attempts.append(True)
        raise KindleStkAuthExpired("revoked")
    monkeypatch.setattr("endless_library.kindle_stk.service.KindleStkService.send_file", fake_send)
    smtp_called = []
    monkeypatch.setattr("endless_library.kindle_router._send_smtp", lambda *a, **kw: smtp_called.append(True))
    from endless_library.kindle_router import deliver
    result = deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)
    assert len(attempts) == 1
    assert smtp_called

def test_router_honors_retry_after(monkeypatch, db, cfg, book, file_path, svc):
    _configure_stk(svc)
    sleep_calls = []
    monkeypatch.setattr("endless_library.kindle_router.time.sleep", lambda s: sleep_calls.append(s))
    attempt_results = [
        KindleStkRateLimited("rl-1", retry_after_sec=2),
        KindleStkRateLimited("rl-2", retry_after_sec=4),
        None,
    ]
    def fake_send(self, file, **kw):
        nxt = attempt_results.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return {"transaction_id": "tx-final"}
    monkeypatch.setattr("endless_library.kindle_stk.service.KindleStkService.send_file", fake_send)
    from endless_library.kindle_router import deliver, DeliveryMethod
    result = deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)
    assert result.method == DeliveryMethod.STK
    assert sleep_calls == [2, 4]

def test_router_records_send_stk_event_on_success(monkeypatch, db, cfg, book, file_path, svc):
    _configure_stk(svc)
    monkeypatch.setattr("endless_library.kindle_stk.service.KindleStkService.send_file", lambda *a, **kw: {"transaction_id": "tx-ok"})
    from endless_library.kindle_router import deliver
    deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)
    with connect(db) as conn:
        rows = conn.execute("SELECT kind FROM events WHERE book_id = ?", (book.id,)).fetchall()
    kinds = [r["kind"] for r in rows]
    assert "send-stk" in kinds

def test_router_records_send_stk_failed_event_on_exhaustion(monkeypatch, db, cfg, book, file_path, svc):
    _configure_stk(svc)
    monkeypatch.setattr(
        "endless_library.kindle_stk.service.KindleStkService.send_file",
        lambda *a, **kw: (_ for _ in ()).throw(KindleStkUploadFailed("nope")),
    )
    monkeypatch.setattr("endless_library.kindle_router._send_smtp", lambda *a, **kw: None)
    from endless_library.kindle_router import deliver
    deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)
    with connect(db) as conn:
        rows = conn.execute("SELECT kind FROM events WHERE book_id = ?", (book.id,)).fetchall()
    kinds = [r["kind"] for r in rows]
    assert "send-stk-failed" in kinds
