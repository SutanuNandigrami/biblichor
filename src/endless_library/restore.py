"""Disaster-recovery restore — the inverse of backup.py.

Loads a .tar.zst (optionally .age) backup from disk OR from the
configured Store, validates the manifest, and atomically swaps the
running biblichor state.

Safety properties:
  - Validates manifest BEFORE touching the live system. If the
    backup is from an incompatible biblichor schema version, refuse
    with a clear error.
  - Refuses to overwrite an existing DB whose latest event is newer
    than the backup, unless --force. (You almost never want to roll
    back; you want to ADD a missing book.)
  - All staged files are checksummed against the manifest before
    swap. A corrupted backup never touches the live data.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from endless_library.backup import (
    DB_SNAPSHOT_NAME,
    MANIFEST_NAME,
    SCHEMA_VERSION,
    BackupManifest,
)
from endless_library.storage.base import Store

log = logging.getLogger(__name__)


class RestoreError(Exception):
    """Any pre-flight validation failure or unrecoverable I/O error."""


@dataclass
class RestoreResult:
    manifest: BackupManifest
    db_restored: bool
    config_restored: bool
    secrets_restored: bool
    library_restored: bool
    files_validated: int
    target_paths: dict[str, Path]


def _decrypt_if_needed(archive: Path, *, age_identity: Path | None) -> Path:
    """If the archive ends in .age, decrypt with the supplied identity
    file. Returns the path to the tar.zst that should be unpacked."""
    if archive.suffix != ".age":
        return archive
    if age_identity is None:
        raise RestoreError(
            "archive is encrypted (.age) but no --age-identity provided"
        )
    if shutil.which("age") is None:
        raise RestoreError("age binary not on PATH")
    out = archive.with_suffix("")
    proc = subprocess.run(
        ["age", "-d", "-i", str(age_identity), "-o", str(out), str(archive)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RestoreError(f"age decrypt failed: {(proc.stderr or proc.stdout)[-400:]}")
    return out


def _extract_tar_zst(archive: Path, dest: Path) -> Path:
    """Decompress + extract. Returns the path to the single contained
    root directory."""
    if shutil.which("zstd") is None:
        raise RestoreError("zstd not on PATH")
    proc = subprocess.Popen(
        ["zstd", "-d", "-c", str(archive)], stdout=subprocess.PIPE
    )
    assert proc.stdout is not None
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tf:
            tf.extractall(path=str(dest))
    finally:
        proc.stdout.close()
        proc.wait()
    if proc.returncode != 0:
        raise RestoreError(f"zstd exit {proc.returncode}")
    # We expect one root directory (made by backup's tf.add(staging, ...))
    roots = [p for p in dest.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise RestoreError(f"expected 1 root dir in archive, found {len(roots)}")
    return roots[0]


def _sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_manifest(root: Path) -> BackupManifest:
    p = root / MANIFEST_NAME
    if not p.exists():
        raise RestoreError(f"manifest missing in archive (expected {p.name})")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise RestoreError(f"manifest is not valid JSON: {e}") from e
    try:
        return BackupManifest(**data)
    except TypeError as e:
        raise RestoreError(f"manifest shape unexpected: {e}") from e


def _validate_compatibility(manifest: BackupManifest) -> None:
    """Refuse outright on schema mismatch — restore from an older
    biblichor would overwrite a newer DB schema."""
    if manifest.schema_version != SCHEMA_VERSION:
        raise RestoreError(
            f"backup schema_version={manifest.schema_version} does not match "
            f"current SCHEMA_VERSION={SCHEMA_VERSION}; restore aborted to "
            f"prevent data loss. Use biblichor-migrate-backup if available."
        )


def _validate_checksums(root: Path, manifest: BackupManifest) -> int:
    """Verify every file enumerated in the manifest still hashes
    correctly. Returns the count of files validated."""
    n = 0
    for rel, expected_sha in manifest.file_checksums.items():
        p = root / rel
        if not p.exists():
            raise RestoreError(f"manifest entry missing on disk: {rel}")
        actual = _sha256_of(p)
        if actual != expected_sha:
            raise RestoreError(
                f"checksum mismatch on {rel}: expected {expected_sha[:8]}…, got {actual[:8]}…"
            )
        n += 1
    return n


def _existing_db_newer(live_db: Path, manifest: BackupManifest) -> bool:
    """True iff the live DB has events that aren't in the backup.
    Approximation: compare events.max(ts). Used to gate `--force`."""
    if not live_db.exists():
        return False
    try:
        with sqlite3.connect(str(live_db)) as conn:
            row = conn.execute("SELECT MAX(ts) FROM events").fetchone()
    except sqlite3.Error:
        return False
    if row is None or row[0] is None:
        return False
    return row[0] > manifest.created_at_utc


def _atomic_swap(staged: Path, live: Path) -> None:
    """Move `staged` -> `live`. If `live` exists, back it up first
    by renaming to `live.bak-YYYYMMDDhhmmssZ`. This way a botched
    restore is still recoverable from the backup-of-the-backup."""
    if live.exists():
        from datetime import datetime, timezone

        bak = live.with_name(
            f"{live.name}.bak-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        )
        live.rename(bak)
        log.info("preserved old %s as %s", live.name, bak.name)
    live.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staged), str(live))


def restore(
    *,
    archive_path: Path,
    db_target: Path,
    config_target: Path,
    secrets_target: Path | None,
    library_target: Path | None,
    age_identity: Path | None = None,
    force: bool = False,
) -> RestoreResult:
    """Restore a biblichor backup.

    All inputs are local paths; if the caller wants to restore from
    a remote Store, they should fetch the archive to a local tempfile
    first.
    """
    if not archive_path.exists():
        raise RestoreError(f"archive not found: {archive_path}")

    target_paths: dict[str, Path] = {}

    with tempfile.TemporaryDirectory(prefix="biblichor-restore-") as work_str:
        work = Path(work_str)

        # 1. Decrypt if .age
        decrypted = _decrypt_if_needed(archive_path, age_identity=age_identity)

        # 2. Extract tar.zst into work/
        root = _extract_tar_zst(decrypted, work)

        # 3. Read + validate manifest
        manifest = _read_manifest(root)
        _validate_compatibility(manifest)

        # 4. Per-file checksum verification BEFORE we touch the live system
        n_validated = _validate_checksums(root, manifest)

        # 5. Detect "live is newer" — refuse unless forced
        if not force and _existing_db_newer(db_target, manifest):
            raise RestoreError(
                "live DB has events newer than the backup; refusing to overwrite. "
                "Re-run with --force if you really want to roll back."
            )

        # 6. Atomic swap each target. The order matters: DB last so that
        # any restart between steps can still find a consistent config
        # pointing at the old DB until the very end.
        cfg_src = root / "config.yaml"
        if cfg_src.exists():
            _atomic_swap(cfg_src, config_target)
            target_paths["config"] = config_target
            cfg_done = True
        else:
            cfg_done = False

        env_src = root / ".env"
        secrets_done = False
        if env_src.exists() and secrets_target is not None:
            _atomic_swap(env_src, secrets_target)
            target_paths["secrets"] = secrets_target
            secrets_done = True

        lib_src = root / "library"
        lib_done = False
        if lib_src.exists() and library_target is not None:
            # For directories, we use a sibling-rename strategy on the
            # target itself, then move the staged dir into place
            if library_target.exists():
                from datetime import datetime, timezone

                bak = library_target.with_name(
                    f"{library_target.name}.bak-"
                    f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
                )
                library_target.rename(bak)
                log.info("preserved old library as %s", bak.name)
            library_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(lib_src), str(library_target))
            target_paths["library"] = library_target
            lib_done = True

        db_src = root / DB_SNAPSHOT_NAME
        db_done = False
        if db_src.exists():
            _atomic_swap(db_src, db_target)
            target_paths["db"] = db_target
            db_done = True

        return RestoreResult(
            manifest=manifest,
            db_restored=db_done,
            config_restored=cfg_done,
            secrets_restored=secrets_done,
            library_restored=lib_done,
            files_validated=n_validated,
            target_paths=target_paths,
        )
