from __future__ import annotations

from pathlib import Path

import pytest

from endless_library.config import KindleCfg, SmtpCfg
from endless_library.kindle import KindleSendError, build_message, send_to_kindle


def test_build_message_attaches_file(tmp_path: Path):
    f = tmp_path / "book.epub"
    f.write_bytes(b"PK\x03\x04dummy-zip")  # epub is a zip
    msg = build_message(
        sender="me@example.com",
        recipient="me@kindle.com",
        subject="The Book",
        body="The Book — Author",
        attachment=f,
    )
    assert msg["Subject"] == "The Book"
    assert msg["To"] == "me@kindle.com"
    parts = [p for p in msg.walk() if p.get_filename()]
    assert len(parts) == 1
    assert parts[0].get_filename() == "book.epub"
    assert parts[0].get_content_maintype() == "application"


def test_send_rejects_oversized(tmp_path: Path):
    f = tmp_path / "huge.epub"
    f.write_bytes(b"x" * (60 * 1024 * 1024))  # 60 MB
    with pytest.raises(KindleSendError, match="exceeds"):
        send_to_kindle(
            attachment=f,
            kindle=KindleCfg(recipient="me@kindle.com", attachment_max_mb=50),
            smtp=SmtpCfg(user="u", password="p"),
            title="x",
        )


def test_send_requires_recipient(tmp_path: Path):
    f = tmp_path / "x.epub"
    f.write_bytes(b"x")
    with pytest.raises(KindleSendError, match="recipient"):
        send_to_kindle(
            attachment=f,
            kindle=KindleCfg(recipient=""),
            smtp=SmtpCfg(user="u", password="p"),
            title="x",
        )


def test_send_requires_smtp_user(tmp_path: Path):
    f = tmp_path / "x.epub"
    f.write_bytes(b"x")
    with pytest.raises(KindleSendError, match="SMTP"):
        send_to_kindle(
            attachment=f,
            kindle=KindleCfg(recipient="me@kindle.com"),
            smtp=SmtpCfg(user=""),
            title="x",
        )
