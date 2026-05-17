from __future__ import annotations

from endless_library.config import PushoverCfg, PushoverEventsCfg
from endless_library.notifier import Notifier


class _Resp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return

    def json(self):
        return self.body


def test_disabled_is_noop():
    sent = []
    n = Notifier(
        PushoverCfg(enabled=False, user_key="u", app_token="t"),
        post=lambda url, data: (sent.append((url, data)), _Resp({"status": 1}))[1],
    )
    n.book_sent("X", "Y", "epub")
    assert sent == []


def test_book_sent_posts():
    sent = []

    def cap(url, data):
        sent.append((url, data))
        return _Resp({"status": 1})

    n = Notifier(
        PushoverCfg(
            enabled=True, user_key="u", app_token="t", events=PushoverEventsCfg(book_sent=True)
        ),
        post=cap,
    )
    n.book_sent("X", "Y", "epub")
    assert len(sent) == 1
    url, data = sent[0]
    assert "messages.json" in url
    assert data["token"] == "t"
    assert "X" in data["message"]


def test_event_toggle_respected():
    sent = []

    def cap(url, data):
        sent.append(data["title"])
        return _Resp({"status": 1})

    n = Notifier(
        PushoverCfg(
            enabled=True,
            user_key="u",
            app_token="t",
            events=PushoverEventsCfg(book_sent=False, book_needs_review=True),
        ),
        post=cap,
    )
    n.book_sent("X", "Y", "epub")
    n.book_needs_review("X", "Y")
    assert sent == ["Book needs review"]


def test_pushover_failure_returns_false_without_raise():
    def cap(url, data):
        return _Resp({"status": 0, "errors": ["bad token"]})

    n = Notifier(
        PushoverCfg(
            enabled=True, user_key="u", app_token="t", events=PushoverEventsCfg(book_sent=True)
        ),
        post=cap,
    )
    # Should not raise
    n.book_sent("X", None, "epub")
