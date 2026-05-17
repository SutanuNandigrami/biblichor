"""Integration test: spin up a local aiosmtpd, send through it, assert receipt."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from aiosmtpd.controller import Controller

from endless_library.config import SmtpCfg
from endless_library.kindle import _send_smtp, build_message


class _Collector:
    def __init__(self):
        self.messages: list[tuple[str, list[str], bytes]] = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append((envelope.mail_from, envelope.rcpt_tos, envelope.content))
        return "250 OK"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_round_trip(tmp_path: Path):
    handler = _Collector()
    port = _free_port()
    controller = Controller(handler, hostname="127.0.0.1", port=port)
    controller.start()
    try:
        f = tmp_path / "book.epub"
        f.write_bytes(b"PK\x03\x04dummy")
        msg = build_message(
            sender="me@example.com",
            recipient="me@kindle.com",
            subject="The Book",
            body="x",
            attachment=f,
        )
        smtp = SmtpCfg(
            host="127.0.0.1", port=port, starttls=False, user="me@example.com", password=""
        )
        # aiosmtpd's default handler doesn't require auth — patch out
        # by sending without credentials
        smtp = SmtpCfg(host="127.0.0.1", port=port, starttls=False, user="", password="")
        # _send_smtp passes username/password regardless; aiosmtpd will accept anyway.
        result = await _send_smtp(msg, smtp=smtp, timeout=10.0)
        assert result.accepted
        assert len(handler.messages) == 1
        _from_addr, rcpts, body = handler.messages[0]
        assert "me@kindle.com" in rcpts
        assert b"The Book" in body  # subject in raw message
    finally:
        controller.stop()
