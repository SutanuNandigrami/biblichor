"""Phase STK-recovery: router sleep throttle test."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from endless_library.db.schema import connect, init_db


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "test.db"
    init_db(p)
    return p


@pytest.fixture
def cfg():
    return SimpleNamespace(
        stk=SimpleNamespace(
            daily_cap=500,
            max_attempts=3,
            backoff_initial_sec=0.0,
            backoff_factor=1.0,
            min_send_interval_sec=5.0,
        ),
        smtp=SimpleNamespace(daily_cap=80),
    )


@pytest.fixture
def book(db):
    with connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO books (title, author, status, source) VALUES (?, ?, ?, ?)",
            ("Sleep Test Book", "Sleep Test Author", "queued", "test"),
        )
        book_id = cur.lastrowid
    return SimpleNamespace(id=book_id, title="Sleep Test Book", author="Sleep Test Author")


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

        def get_secret_value(self, key):
            return self._secrets.get(key)

        def set_secret_value(self, key, value):
            self._secrets[key] = value

        def set_secret_values(self, kv):
            self._secrets.update(kv)

        def delete_secret_value(self, key):
            self._secrets.pop(key, None)

    return _Svc()


def _configure_stk(svc):
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    svc.set_secret_value("kindle_stk.default_destination_sn", "G0WEB1")
    svc.set_secret_value("kindle_stk.default_destination_name", "Kindle for Web")


def test_router_sleeps_min_send_interval_before_stk_attempt(
    monkeypatch, db, cfg, book, file_path, svc
):
    """deliver() must call time.sleep(cfg.stk.min_send_interval_sec) once before
    the STK send attempt, enforcing the anti-abuse throttle."""
    _configure_stk(svc)

    sleep_calls = []
    monkeypatch.setattr("endless_library.kindle_router.time.sleep", lambda s: sleep_calls.append(s))

    monkeypatch.setattr(
        "endless_library.kindle_stk.service.KindleStkService.send_file",
        lambda *a, **kw: {"transaction_id": "tx-throttle"},
    )

    from endless_library.kindle_router import deliver, DeliveryMethod

    result = deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)

    assert result.ok is True
    assert result.method == DeliveryMethod.STK
    # The throttle sleep must have been called with the configured value
    assert cfg.stk.min_send_interval_sec in sleep_calls, (
        f"Expected sleep({cfg.stk.min_send_interval_sec}) but got sleep calls: {sleep_calls}"
    )
