"""Hybrid two-backend store: primary + backup, with two write modes.

  mode="mirror"   put() writes to both stores synchronously. If the
                  backup write fails, log + continue (don't fail the
                  primary write). Reads always come from primary.

  mode="scheduled" put() writes only to primary. A separate
                   APScheduler job (registered in scheduler.py) walks
                   the primary periodically and pushes new keys to
                   the backup. Reads from primary.

Both modes share the same Store interface, so callers don't know or
care which mode is configured.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator, Literal

from endless_library.storage.base import KeyNotFound, StorageError, Store

log = logging.getLogger(__name__)

HybridMode = Literal["mirror", "scheduled"]


class HybridStore:
    name = "hybrid"

    def __init__(
        self,
        primary: Store,
        backup: Store,
        *,
        mode: HybridMode = "mirror",
    ) -> None:
        self.primary = primary
        self.backup = backup
        self.mode = mode

    def put(self, local_path: Path, remote_key: str) -> None:
        # Primary write is authoritative — its failure propagates
        self.primary.put(local_path, remote_key)
        if self.mode != "mirror":
            return
        # Backup write is best-effort; failures don't block the caller
        try:
            self.backup.put(local_path, remote_key)
        except StorageError as e:
            log.warning(
                "hybrid backup put failed for %s (key=%s): %s — primary OK",
                self.backup.name, remote_key, e,
            )

    def get(self, remote_key: str, local_path: Path) -> None:
        # Prefer primary; fall back to backup only on KeyNotFound, not on
        # transient errors — those should bubble up so the caller knows
        # something's wrong with the primary.
        try:
            self.primary.get(remote_key, local_path)
            return
        except KeyNotFound:
            log.info("primary missing %s, trying backup", remote_key)
        self.backup.get(remote_key, local_path)  # may itself raise

    def exists(self, remote_key: str) -> bool:
        # Either side counts as existing — this matches "do I need to
        # upload again?" semantics. A future API could distinguish
        # primary-only vs backup-only.
        return self.primary.exists(remote_key) or self.backup.exists(remote_key)

    def delete(self, remote_key: str) -> None:
        # Idempotent: delete from both sides. We track both failures
        # but never raise for a missing key (per Store contract).
        primary_err: Exception | None = None
        backup_err: Exception | None = None
        try:
            self.primary.delete(remote_key)
        except StorageError as e:
            primary_err = e
        try:
            self.backup.delete(remote_key)
        except StorageError as e:
            backup_err = e
        if primary_err is not None:
            # Primary error is fatal — caller wanted that delete
            raise primary_err
        if backup_err is not None:
            # Don't fail the call but record the asymmetry
            log.warning("hybrid backup delete failed for %s: %s", remote_key, backup_err)

    def list(self, prefix: str = "") -> Iterator[str]:
        # Return the union, deduped + sorted, so consumers see one
        # logical namespace.
        seen: set[str] = set()
        for k in self.primary.list(prefix):
            seen.add(k)
        try:
            for k in self.backup.list(prefix):
                seen.add(k)
        except StorageError as e:
            log.warning("hybrid backup list failed (primary keys returned): %s", e)
        yield from sorted(seen)
