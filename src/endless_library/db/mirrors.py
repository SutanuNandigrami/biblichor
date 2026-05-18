"""mirrors table repo + curated registry seeding."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .schema import connect


@dataclass(frozen=True, slots=True)
class MirrorRow:
    id: int
    kind: str
    url: str
    label: str | None
    enabled: bool
    consecutive_failures: int
    last_probed_at: str | None
    last_ok_at: str | None
    last_status: int | None
    last_latency_ms: int | None
    last_error: str | None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> MirrorRow:
        return cls(
            id=r["id"],
            kind=r["kind"],
            url=r["url"],
            label=r["label"],
            enabled=bool(r["enabled"]),
            consecutive_failures=r["consecutive_failures"],
            last_probed_at=r["last_probed_at"],
            last_ok_at=r["last_ok_at"],
            last_status=r["last_status"],
            last_latency_ms=r["last_latency_ms"],
            last_error=r["last_error"],
        )


class MirrorRepo:
    AUTO_DISABLE_AFTER = 5  # consecutive failures

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def seed_curated(self) -> int:
        """Refresh the curated mirror set.

        - Every entry in CURATED_MIRRORS that isn't already in the DB
          gets INSERTed. Existing rows are not touched (the user may
          have disabled them or renamed the label).
        - Any URL in LEGACY_CURATED that IS in the DB and isn't in the
          current CURATED_MIRRORS list gets auto-disabled (enabled=0).
          We never DELETE — the user can re-enable from the Mirrors
          page if a legacy URL comes back.

        Returns the number of new inserts.
        """
        from endless_library.probes import CURATED_MIRRORS, LEGACY_CURATED

        current_urls = {e["url"] for e in CURATED_MIRRORS}
        inserted = 0
        with connect(self.db_path) as conn:
            # Insert anything new
            for entry in CURATED_MIRRORS:
                row = conn.execute(
                    "SELECT id FROM mirrors WHERE url = ?", (entry["url"],)
                ).fetchone()
                if row:
                    continue
                conn.execute(
                    "INSERT INTO mirrors (kind, url, label) VALUES (?, ?, ?)",
                    (entry["kind"], entry["url"], entry["label"]),
                )
                inserted += 1
            # Auto-disable legacy URLs we have retired
            for legacy_url in LEGACY_CURATED:
                if legacy_url in current_urls:
                    continue  # somehow still in the curated list, leave alone
                conn.execute(
                    "UPDATE mirrors SET enabled = 0 WHERE url = ? AND enabled = 1",
                    (legacy_url,),
                )
        return inserted

    def list_all(self, *, kind: str | None = None) -> list[MirrorRow]:
        with connect(self.db_path) as conn:
            if kind:
                rows = conn.execute(
                    "SELECT * FROM mirrors WHERE kind = ? ORDER BY id", (kind,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM mirrors ORDER BY id").fetchall()
        return [MirrorRow.from_row(r) for r in rows]

    def get(self, mirror_id: int) -> MirrorRow | None:
        with connect(self.db_path) as conn:
            r = conn.execute("SELECT * FROM mirrors WHERE id = ?", (mirror_id,)).fetchone()
        return MirrorRow.from_row(r) if r else None

    def add(self, *, kind: str, url: str, label: str | None) -> int:
        with connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO mirrors (kind, url, label) VALUES (?, ?, ?)",
                (kind, url, label),
            )
            return int(cur.lastrowid)

    def delete(self, mirror_id: int) -> None:
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM mirrors WHERE id = ?", (mirror_id,))

    def set_enabled(self, mirror_id: int, enabled: bool) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE mirrors SET enabled = ?, consecutive_failures = 0 WHERE id = ?",
                (1 if enabled else 0, mirror_id),
            )

    def record_probe(
        self,
        mirror_id: int,
        *,
        ok: bool,
        status: int | None,
        latency_ms: int | None,
        error: str | None,
    ) -> None:
        with connect(self.db_path) as conn:
            if ok:
                conn.execute(
                    """UPDATE mirrors
                          SET last_probed_at = datetime('now'),
                              last_ok_at     = datetime('now'),
                              last_status    = ?,
                              last_latency_ms = ?,
                              last_error     = NULL,
                              consecutive_failures = 0
                        WHERE id = ?""",
                    (status, latency_ms, mirror_id),
                )
            else:
                conn.execute(
                    """UPDATE mirrors
                          SET last_probed_at = datetime('now'),
                              last_status    = ?,
                              last_latency_ms = ?,
                              last_error     = ?,
                              consecutive_failures = consecutive_failures + 1
                        WHERE id = ?""",
                    (status, latency_ms, error, mirror_id),
                )
                # Auto-disable after AUTO_DISABLE_AFTER
                conn.execute(
                    """UPDATE mirrors
                          SET enabled = 0
                        WHERE id = ?
                          AND consecutive_failures >= ?""",
                    (mirror_id, self.AUTO_DISABLE_AFTER),
                )

    def healthy_urls(self, *, kind: str) -> list[str]:
        rows = self.list_all(kind=kind)
        return [r.url for r in rows if r.enabled]
