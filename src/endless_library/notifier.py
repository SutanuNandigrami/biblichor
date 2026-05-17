from __future__ import annotations

import logging
from collections.abc import Callable

import httpx

from endless_library.config import PushoverCfg

log = logging.getLogger(__name__)

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


class Notifier:
    """Pushover-backed notifier. No-op when disabled or unconfigured."""

    def __init__(
        self,
        cfg: PushoverCfg,
        *,
        post: Callable[[str, dict], httpx.Response] | None = None,
    ) -> None:
        self.cfg = cfg
        self._post = post

    def _send(self, title: str, message: str, *, priority: int = 0) -> bool:
        if not self.cfg.enabled or not self.cfg.user_key or not self.cfg.app_token:
            log.debug("pushover disabled; would have sent: %s", title)
            return False
        data = {
            "token": self.cfg.app_token,
            "user": self.cfg.user_key,
            "title": title,
            "message": message,
            "priority": priority,
        }
        try:
            if self._post is not None:
                r = self._post(PUSHOVER_URL, data)
            else:
                r = httpx.post(PUSHOVER_URL, data=data, timeout=15.0)
            r.raise_for_status()
            body = r.json()
            ok = body.get("status") == 1
            if not ok:
                log.warning("pushover failure: %s", body)
            return ok
        except Exception as e:
            log.warning("pushover error: %s", e)
            return False

    def book_sent(self, title: str, author: str | None, fmt: str) -> None:
        if not self.cfg.events.book_sent:
            return
        self._send(
            title="Book sent to Kindle",
            message=f"{title}{(' — ' + author) if author else ''} ({fmt})",
        )

    def book_needs_review(self, title: str, author: str | None) -> None:
        if not self.cfg.events.book_needs_review:
            return
        self._send(
            title="Book needs review",
            message=f"{title}{(' — ' + author) if author else ''} — score too low; pick manually.",
            priority=1,
        )

    def daily_summary(self, *, sent: int, failed: int, needs_review: int) -> None:
        if not self.cfg.events.cycle_summary:
            return
        self._send(
            title="endless-library daily summary",
            message=f"sent {sent} | failed {failed} | needs_review {needs_review}",
        )
