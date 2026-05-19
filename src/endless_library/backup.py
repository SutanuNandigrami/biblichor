"""Disaster-recovery backups for biblichor.

A backup is a single .tar.zst (optionally age-encrypted) containing:

  - sqlite snapshot of library.db (WAL-checkpointed, then .backup)
  - config/config.yaml
  - config/.env (secrets)
  - data/books/ (the entire library)
  - manifest.json — versions, schema hash, per-file checksums

The bundle is pushed through the configured Storage backend so it
ends up on Drive / Hetzner / wherever the user wants. Restore is
the inverse of this function — see `restore.py`.

Why JSON for the manifest? Restore needs to validate manifest
BEFORE unpacking anything, and JSON parses without any biblichor
imports — letting the restore CLI fail early on an incompatible
backup before it stops the running service.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from endless_library.storage.base import Store

log = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
DB_SNAPSHOT_NAME = "library.db"
SCHEMA_VERSION = 1  # bump on schema migrations


class BackupError(Exception):
    """Any backup-time failure (snapshot, archive, push)."""


@dataclass
class BackupManifest:
    """What a restore needs to know BEFORE touching the running system.

    Stored as JSON inside the archive at MANIFEST_NAME. Parsed by the
    restore CLI without importing biblichor modules, so a future
    biblichor refactor can't break old backups.
    """

    biblichor_version: str
    python_version: str
    platform: str
    created_at_utc: str
    schema_version: int
    file_checksums: dict[str, str] = field(default_factory=dict)  # rel_path -> sha256
    encrypted: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _wal_checkpoint(db_path: Path) -> None:
    """Truncate the WAL into the main DB so the snapshot is whole."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _snapshot_db(src: Path, dst: Path) -> None:
    """Use sqlite's online .backup so a concurrent writer can't corrupt
    the snapshot mid-copy."""
    _wal_checkpoint(src)
    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()


def _build_manifest(staging: Path) -> BackupManifest:
    from endless_library import __version__ as bv

    manifest = BackupManifest(
        biblichor_version=bv,
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        created_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        schema_version=SCHEMA_VERSION,
    )
    for root, _dirs, files in os.walk(staging):
        for fname in files:
            if fname == MANIFEST_NAME:
                continue
            p = Path(root) / fname
            rel = str(p.relative_to(staging)).replace(os.sep, "/")
            manifest.file_checksums[rel] = _sha256_of(p)
    return manifest


def _make_tar_zst(staging: Path, output_path: Path) -> None:
    """tar -cf - <staging> | zstd > output_path
    We use the `zstd` binary for the compression — pure-python zstd
    libs exist but adding a dep for a one-call command is overkill.
    """
    if shutil.which("zstd") is None:
        raise BackupError("zstd not on PATH (install it via apt/brew)")
    # Build tar in memory-ish: stream via subprocess pipeline
    with open(output_path, "wb") as out_f:
        zstd = subprocess.Popen(
            ["zstd", "-T0", "-19", "--no-progress", "-"], stdin=subprocess.PIPE, stdout=out_f
        )
        assert zstd.stdin is not None
        with tarfile.open(fileobj=zstd.stdin, mode="w|") as tf:
            # Add the staging dir itself so tar xf produces a contained dir
            tf.add(staging, arcname=staging.name)
        zstd.stdin.close()
        rc = zstd.wait()
        if rc != 0:
            raise BackupError(f"zstd exit {rc}")


def _maybe_encrypt(archive: Path, *, recipient: str | None) -> Path:
    """Run age encryption if a recipient public key is configured.
    Returns the archive path that should be uploaded (possibly the
    same file, possibly a new .age sibling)."""
    if not recipient:
        return archive
    if shutil.which("age") is None:
        raise BackupError("storage.backup_recipient is set but `age` not on PATH")
    encrypted = archive.with_suffix(archive.suffix + ".age")
    proc = subprocess.run(
        ["age", "-r", recipient, "-o", str(encrypted), str(archive)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise BackupError(f"age encrypt failed: {(proc.stderr or proc.stdout)[-400:]}")
    archive.unlink(missing_ok=True)
    return encrypted


@dataclass
class BackupResult:
    remote_key: str
    bytes_written: int
    manifest: BackupManifest
    archive_path: Path


def make_backup(
    *,
    db_path: Path,
    config_path: Path,
    secrets_path: Path | None,
    library_dir: Path | None,
    store: Store,
    age_recipient: str | None = None,
    remote_prefix: str = "backups",
    postgres_dump_cmd: list[str] | None = None,
    bookorbit_data_dir: Path | None = None,
) -> BackupResult:
    """Create a backup and push it through `store`.

    Args:
      db_path: live sqlite DB path.
      config_path: config/config.yaml to include.
      secrets_path: optional config/.env. Skipped if None.
      library_dir: optional books directory. Skipped if None.
      store: the destination Store (any backend).
      age_recipient: public age key. None = unencrypted (NOT
        recommended; backups contain SMTP password etc.).
      remote_prefix: where to land the bundle on the destination
        backend (default "backups/").
      postgres_dump_cmd: optional argv to run for the BookOrbit
        Postgres dump (Phase 6g). The command's stdout is captured
        into the backup as postgres.sql. Example:
        ["docker", "compose", "exec", "-T", "bookorbit-db",
         "pg_dump", "-U", "bookorbit", "bookorbit"]
        Skipped if None.
      bookorbit_data_dir: optional path to BookOrbit's /data
        directory contents (covers + book-bucket). Skipped if None.
    """
    if not db_path.exists():
        raise BackupError(f"db not found: {db_path}")
    if not config_path.exists():
        raise BackupError(f"config not found: {config_path}")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fname_root = f"biblichor-backup-{ts}"

    with tempfile.TemporaryDirectory(prefix="biblichor-backup-") as work_str:
        work = Path(work_str)
        staging = work / fname_root
        staging.mkdir()

        # 1. DB snapshot
        log.info("backup: snapshotting db")
        _snapshot_db(db_path, staging / DB_SNAPSHOT_NAME)

        # 2. Config
        shutil.copy2(config_path, staging / "config.yaml")

        # 3. Secrets (optional)
        if secrets_path and secrets_path.exists():
            shutil.copy2(secrets_path, staging / ".env")

        # 4. Library (optional, can be huge)
        if library_dir and library_dir.exists():
            log.info("backup: copying library from %s", library_dir)
            shutil.copytree(
                library_dir, staging / "library", dirs_exist_ok=True
            )

        # 4b. Postgres dump (Phase 6g) — captures BookOrbit's entire
        # state (users, libraries, sync, audit, etc.) into postgres.sql.
        # We deliberately run AFTER files are staged so a failure here
        # doesn't waste the library copy step (which is the big one).
        if postgres_dump_cmd:
            log.info("backup: running pg_dump (%s)", postgres_dump_cmd[0])
            try:
                proc = subprocess.run(
                    postgres_dump_cmd,
                    capture_output=True,
                    timeout=600,
                    check=False,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                raise BackupError(f"pg_dump failed: {e}") from e
            if proc.returncode != 0:
                tail = proc.stderr[-400:].decode("utf-8", errors="replace")
                raise BackupError(f"pg_dump exit {proc.returncode}: {tail}")
            (staging / "postgres.sql").write_bytes(proc.stdout)

        # 4c. BookOrbit data dir (covers + book-bucket)
        if bookorbit_data_dir and bookorbit_data_dir.exists():
            log.info("backup: copying bookorbit data from %s", bookorbit_data_dir)
            shutil.copytree(
                bookorbit_data_dir, staging / "bookorbit-data", dirs_exist_ok=True
            )

        # 5. Manifest LAST so it includes everyone else's checksums
        manifest = _build_manifest(staging)
        manifest.encrypted = bool(age_recipient)
        (staging / MANIFEST_NAME).write_text(manifest.to_json())

        # 6. Archive + optional encrypt
        archive = work / f"{fname_root}.tar.zst"
        log.info("backup: tar+zst -> %s", archive.name)
        _make_tar_zst(staging, archive)
        final = _maybe_encrypt(archive, recipient=age_recipient)

        # 7. Push through Store
        remote_key = f"{remote_prefix.strip('/')}/{final.name}"
        bytes_written = final.stat().st_size
        log.info("backup: pushing to %s (%d bytes) as %s", store.name, bytes_written, remote_key)
        store.put(final, remote_key)

        # Local copy stays where Store wrote it; the temp staging dir is
        # the only thing cleaned up.
        return BackupResult(
            remote_key=remote_key,
            bytes_written=bytes_written,
            manifest=manifest,
            archive_path=final,
        )
