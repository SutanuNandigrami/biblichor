"""Migrate every key from one Store to another.

Idempotent: re-running picks up where the last run left off
(skipping keys already present on the destination). Resumes cleanly
after interruption — there's no in-memory state to lose.

This is what `biblichor storage migrate --to <backend>` ultimately
calls.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from endless_library.storage.base import KeyNotFound, StorageError, Store

log = logging.getLogger(__name__)


@dataclass
class MigrateResult:
    total: int
    copied: int
    skipped_existing: int
    failed: int
    errors: list[tuple[str, str]]  # (remote_key, error_message)


def migrate_all(
    src: Store,
    dst: Store,
    *,
    prefix: str = "",
    overwrite: bool = False,
    on_progress=None,
) -> MigrateResult:
    """Walk every key in `src.list(prefix)` and copy it into `dst`.

    Skips keys that already exist on `dst` unless overwrite=True.
    Uses a temporary file in the system temp dir as the staging
    area; cleans it up between keys so we never blow disk.

    on_progress: optional callable(remote_key, copied_count, total)
                 fired after each successful copy. Used by the CLI
                 to print a progress line.
    """
    keys = list(src.list(prefix))
    total = len(keys)
    copied = skipped = failed = 0
    errors: list[tuple[str, str]] = []
    log.info("migrate: %d keys to copy (%s -> %s, prefix=%r)", total, src.name, dst.name, prefix)
    for key in keys:
        if not overwrite and dst.exists(key):
            skipped += 1
            log.debug("migrate skip (exists on dst): %s", key)
            continue
        try:
            with tempfile.NamedTemporaryFile(prefix="biblichor-migrate-", delete=False) as tmp:
                staging = Path(tmp.name)
            try:
                src.get(key, staging)
                dst.put(staging, key)
                copied += 1
                if on_progress is not None:
                    on_progress(key, copied, total)
            finally:
                staging.unlink(missing_ok=True)
        except (StorageError, KeyNotFound, OSError) as e:
            failed += 1
            errors.append((key, str(e)))
            log.warning("migrate fail %s: %s", key, e)
    return MigrateResult(
        total=total, copied=copied, skipped_existing=skipped, failed=failed, errors=errors
    )
