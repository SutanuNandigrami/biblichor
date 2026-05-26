"""STK delivery rate-limit gate.

Mirrors smtp_rate.py — counts events with kind='send-stk' in a rolling
24h window. Daily cap is supplied by the caller (typically
cfg.stk.daily_cap).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .db.schema import connect


@dataclass(frozen=True, slots=True)
class StkQuotaStatus:
    sent_24h: int
    cap: int
    remaining: int
    exhausted: bool


def quota_status(db_path: Path, *, daily_cap: int) -> StkQuotaStatus:
    """Count rows in events with kind='send-stk' in the rolling 24h
    window. Returns a status struct the router + healthz can both use.
    """
    with connect(db_path) as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM events
               WHERE kind = 'send-stk'
                 AND ts >= datetime('now', '-1 day')""",
        ).fetchone()
    n = int(row["n"] or 0)
    return StkQuotaStatus(
        sent_24h=n,
        cap=daily_cap,
        remaining=max(0, daily_cap - n),
        exhausted=n >= daily_cap,
    )
