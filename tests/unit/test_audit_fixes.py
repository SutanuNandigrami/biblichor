"""Tests for the audit fixes — SMTP exception translation + retention prune.

These pin down failures we observed in production (book #62 SMTP 552
pipeline crash) AND prevent regressions on retention.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import patch

import aiosmtplib
import pytest

from endless_library.config import SmtpCfg
from endless_library.db.bench import BenchRunRepo
from endless_library.db.events import EventRepo
from endless_library.db.schema import init_db
from endless_library.kindle import KindleSendError, _send_smtp, build_message

# ============ SMTP exception translation ============


def _build_dummy_msg(tmp_path: Path):
    attach = tmp_path / "x.epub"
    attach.write_bytes(b"PK\x03\x04dummy")
    return build_message(
        sender="me@example.com",
        recipient="you@kindle.com",
        subject="x",
        body="x",
        attachment=attach,
    )


def test_smtp_data_error_552_translates_to_size_message(tmp_path):
    msg = _build_dummy_msg(tmp_path)
    smtp = SmtpCfg(host="smtp.example.com", port=587, user="u", password="p")

    err = aiosmtplib.SMTPDataError(552, "5.3.4 Your message exceeded size limits")
    with (
        patch("aiosmtplib.send", side_effect=err),
        pytest.raises(KindleSendError, match="too large"),
    ):
        asyncio.run(_send_smtp(msg, smtp=smtp))


def test_smtp_auth_error_translates_with_actionable_message(tmp_path):
    msg = _build_dummy_msg(tmp_path)
    smtp = SmtpCfg(host="smtp.example.com", port=587, user="u", password="p")

    err = aiosmtplib.SMTPAuthenticationError(535, "5.7.8 Username and Password not accepted")
    with (
        patch("aiosmtplib.send", side_effect=err),
        pytest.raises(KindleSendError, match="authentication failed"),
    ):
        asyncio.run(_send_smtp(msg, smtp=smtp))


def test_smtp_recipient_refused_mentions_amazon_list(tmp_path):
    msg = _build_dummy_msg(tmp_path)
    smtp = SmtpCfg(host="smtp.example.com", port=587, user="u", password="p")

    err = aiosmtplib.SMTPRecipientsRefused([("rejected", 550, "not on approved list")])
    with (
        patch("aiosmtplib.send", side_effect=err),
        pytest.raises(KindleSendError, match="Approved Personal Document"),
    ):
        asyncio.run(_send_smtp(msg, smtp=smtp))


def test_smtp_connect_error_translates(tmp_path):
    msg = _build_dummy_msg(tmp_path)
    smtp = SmtpCfg(host="unreachable.example.com", port=587, user="u", password="p")

    err = aiosmtplib.SMTPConnectError("Connection refused")
    with (
        patch("aiosmtplib.send", side_effect=err),
        pytest.raises(KindleSendError, match="Could not connect to SMTP"),
    ):
        asyncio.run(_send_smtp(msg, smtp=smtp))


def test_smtp_timeout_translates(tmp_path):
    msg = _build_dummy_msg(tmp_path)
    smtp = SmtpCfg(host="slow.example.com", port=587, user="u", password="p")

    err = aiosmtplib.SMTPTimeoutError("Timed out")
    with (
        patch("aiosmtplib.send", side_effect=err),
        pytest.raises(KindleSendError, match="timed out"),
    ):
        asyncio.run(_send_smtp(msg, smtp=smtp))


def test_smtp_generic_smtp_exception_still_caught(tmp_path):
    """Any aiosmtplib subclass we didn't enumerate must still become a
    KindleSendError, not bubble raw."""
    msg = _build_dummy_msg(tmp_path)
    smtp = SmtpCfg(host="x.example.com", port=587, user="u", password="p")

    err = aiosmtplib.SMTPHeloError(530, "Must issue a STARTTLS command first")
    with (
        patch("aiosmtplib.send", side_effect=err),
        pytest.raises(KindleSendError, match="SMTP error"),
    ):
        asyncio.run(_send_smtp(msg, smtp=smtp))


def test_smtp_errors_dict_still_handled(tmp_path):
    """The non-exception 'errors' return from aiosmtplib.send is still surfaced."""
    msg = _build_dummy_msg(tmp_path)
    smtp = SmtpCfg(host="x.example.com", port=587, user="u", password="p")
    with (
        patch("aiosmtplib.send", return_value=({"r": "bad"}, "ok")),
        pytest.raises(KindleSendError, match="SMTP errors"),
    ):
        asyncio.run(_send_smtp(msg, smtp=smtp))


def test_smtp_ok_path(tmp_path):
    msg = _build_dummy_msg(tmp_path)
    smtp = SmtpCfg(host="x.example.com", port=587, user="u", password="p")
    with patch("aiosmtplib.send", return_value=({}, "250 OK")):
        result = asyncio.run(_send_smtp(msg, smtp=smtp))
    assert result.accepted
    assert "OK" in result.response


# ============ EventRepo.prune ============


def _seed_events(db_path: Path, n: int) -> EventRepo:
    init_db(db_path)
    repo = EventRepo(db_path)
    with sqlite3.connect(db_path) as conn:
        for i in range(n):
            conn.execute(
                "INSERT INTO events (book_id, kind, message, ts) VALUES (?, ?, ?, "
                "datetime('now', '-' || ? || ' days'))",
                (None, "test", f"msg {i}", i),
            )
        conn.commit()
    return repo


def test_prune_drops_events_older_than_keep_days(tmp_path):
    repo = _seed_events(tmp_path / "library.db", n=200)
    # 200 events spanning 0..199 days old
    deleted = repo.prune(keep_rows=100_000, keep_days=30)
    assert deleted == 200 - 31  # rows 31..199 are older than 30 days; 0..30 stay
    with sqlite3.connect(tmp_path / "library.db") as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert remaining == 31


def test_prune_caps_total_rows(tmp_path):
    repo = _seed_events(tmp_path / "library.db", n=200)
    # keep_days large so age doesn't trigger; cap at 50 rows
    deleted = repo.prune(keep_rows=50, keep_days=99_999)
    assert deleted == 150
    with sqlite3.connect(tmp_path / "library.db") as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert remaining == 50


def test_prune_both_age_and_count_cuts(tmp_path):
    """When both age cut AND row cap trim, prune deletes both."""
    repo = _seed_events(tmp_path / "library.db", n=200)
    deleted = repo.prune(keep_rows=20, keep_days=30)
    # 169 age-pruned, then count-prune trims down to 20 from the remaining 31
    assert deleted >= 180
    with sqlite3.connect(tmp_path / "library.db") as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert remaining == 20


def test_prune_idempotent_on_empty(tmp_path):
    init_db(tmp_path / "library.db")
    repo = EventRepo(tmp_path / "library.db")
    assert repo.prune(keep_rows=100, keep_days=30) == 0


# ============ BenchRunRepo.prune ============


def test_bench_prune_keeps_last_n_per_scraper(tmp_path):
    init_db(tmp_path / "library.db")
    repo = BenchRunRepo(tmp_path / "library.db")
    # 300 rows for scraper A, 50 for scraper B
    for i in range(300):
        repo.record(scraper="A", query=f"q{i}", success=True, duration_ms=100)
    for i in range(50):
        repo.record(scraper="B", query=f"q{i}", success=True, duration_ms=200)
    deleted = repo.prune(keep_per_scraper=100)
    assert deleted == 200  # A trimmed from 300 to 100; B was already under
    with sqlite3.connect(tmp_path / "library.db") as conn:
        a = conn.execute("SELECT COUNT(*) FROM bench_runs WHERE scraper = 'A'").fetchone()[0]
        b = conn.execute("SELECT COUNT(*) FROM bench_runs WHERE scraper = 'B'").fetchone()[0]
    assert a == 100 and b == 50
