"""SMTP rate-limit gate.

biblichor's Kindle send path uses one SMTP account (Gmail by default).
Gmail's outbound caps are roughly:
  - Free Gmail:  ~100 messages / rolling 24 h
  - Workspace:  ~2000 messages / 24 h

When we queue a large catalog source (Phase 6u kindlebangla) we can
realistically hit the cap in a single backfill. Without a gate, every
queued book hits 421/452/550 and the pipeline marks them failed,
exhausting the retry budget.

This module gives the pipeline a single `check_can_send()` helper that
counts kind="send" events in the last 24 h. When the count is at or
above `daily_cap`, the caller defers the send (the book stays in
picked/searched state) and the next cycle retries — effectively
self-pacing to the configured budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from endless_library.db.schema import connect

WINDOW_SQL = """
SELECT COUNT(*) FROM events
 WHERE kind = 'send'
   AND ts >= datetime('now', '-1 day')
"""


@dataclass(frozen=True, slots=True)
class SmtpQuotaStatus:
    sent_24h: int
    cap: int

    @property
    def remaining(self) -> int:
        return max(0, self.cap - self.sent_24h)

    @property
    def exhausted(self) -> bool:
        return self.cap > 0 and self.sent_24h >= self.cap


def quota_status(db_path: Path | str, *, daily_cap: int) -> SmtpQuotaStatus:
    """Return (sent_in_last_24h, cap). Counts kind='send' events.

    A cap of 0 disables the gate entirely (always returns remaining=∞,
    exhausted=False).
    """
    if daily_cap <= 0:
        return SmtpQuotaStatus(sent_24h=0, cap=0)
    with connect(Path(db_path)) as conn:
        row = conn.execute(WINDOW_SQL).fetchone()
    sent = int(row[0]) if row else 0
    return SmtpQuotaStatus(sent_24h=sent, cap=daily_cap)
