"""Tests for `biblichor backup` (Phase 5a).

We mock out age encryption + zstd in some tests so the units run
fast in CI without those binaries; one functional test exercises
the real pipeline if zstd is present.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from endless_library.backup import (
    MANIFEST_NAME,
    BackupError,
    BackupManifest,
    BackupResult,
    make_backup,
)
from endless_library.storage.local import LocalStore


@pytest.fixture
def fake_world(tmp_path: Path):
    """Build a fake biblichor workspace: db + config + secrets + library."""
    db = tmp_path / "library.db"
    import sqlite3

    with sqlite3.connect(str(db)) as conn:
        conn.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO books (id, title) VALUES (1, 'টেস্ট বুক')")

    cfg = tmp_path / "config.yaml"
    cfg.write_text("general:\n  books_dir: /var/lib/biblichor\n")
    env = tmp_path / ".env"
    env.write_text("GMAIL_USER=demo@gmail.com\nGMAIL_APP_PASSWORD=abcd\n")
    lib = tmp_path / "library"
    lib.mkdir()
    (lib / "book1.epub").write_bytes(b"epub bytes for book 1")
    (lib / "book2.pdf").write_bytes(b"pdf bytes for book 2 -- bigger" * 100)

    store = LocalStore(tmp_path / "backup-dest")
    return {
        "db": db,
        "cfg": cfg,
        "env": env,
        "lib": lib,
        "store": store,
        "tmp": tmp_path,
    }


def _is_zstd_available() -> bool:
    import shutil

    return shutil.which("zstd") is not None


# ============ REGRESSION: missing required inputs raise BackupError ============


def test_missing_db_raises_backup_error(tmp_path, fake_world):
    fake_world["db"].unlink()
    with pytest.raises(BackupError, match="db not found"):
        make_backup(
            db_path=fake_world["db"],
            config_path=fake_world["cfg"],
            secrets_path=fake_world["env"],
            library_dir=fake_world["lib"],
            store=fake_world["store"],
        )


def test_missing_config_raises_backup_error(fake_world):
    fake_world["cfg"].unlink()
    with pytest.raises(BackupError, match="config not found"):
        make_backup(
            db_path=fake_world["db"],
            config_path=fake_world["cfg"],
            secrets_path=fake_world["env"],
            library_dir=fake_world["lib"],
            store=fake_world["store"],
        )


def test_missing_zstd_raises_backup_error(fake_world, monkeypatch):
    """Without zstd on PATH we can't archive — fail fast with a clear
    message instead of producing garbage."""
    monkeypatch.setattr("shutil.which", lambda x: None if x == "zstd" else "/usr/bin/" + x)
    with pytest.raises(BackupError, match="zstd not on PATH"):
        make_backup(
            db_path=fake_world["db"],
            config_path=fake_world["cfg"],
            secrets_path=fake_world["env"],
            library_dir=fake_world["lib"],
            store=fake_world["store"],
        )


def test_age_recipient_without_age_binary_raises(fake_world, monkeypatch):
    """If user configures encryption but `age` is missing, surface
    BEFORE writing the unencrypted bundle to disk."""
    # zstd available, age missing
    monkeypatch.setattr(
        "shutil.which",
        lambda x: None if x == "age" else f"/usr/bin/{x}",
    )
    if not _is_zstd_available():
        pytest.skip("real zstd needed for this path")
    with pytest.raises(BackupError, match="age.*not on PATH"):  # noqa: RUF043
        make_backup(
            db_path=fake_world["db"],
            config_path=fake_world["cfg"],
            secrets_path=fake_world["env"],
            library_dir=fake_world["lib"],
            store=fake_world["store"],
            age_recipient="age1fake0recipient",
        )


# ============ FEATURE-INTACT: happy path produces a valid archive ============


@pytest.mark.skipif(not _is_zstd_available(), reason="zstd binary not present")
def test_backup_round_trips_full_world(fake_world):
    result = make_backup(
        db_path=fake_world["db"],
        config_path=fake_world["cfg"],
        secrets_path=fake_world["env"],
        library_dir=fake_world["lib"],
        store=fake_world["store"],
        age_recipient=None,
        remote_prefix="backups",
    )
    assert isinstance(result, BackupResult)
    assert result.bytes_written > 0
    assert result.remote_key.startswith("backups/biblichor-backup-")
    assert result.remote_key.endswith(".tar.zst")
    assert not result.manifest.encrypted

    # The archive was actually pushed to the store
    assert fake_world["store"].exists(result.remote_key)


@pytest.mark.skipif(not _is_zstd_available(), reason="zstd binary not present")
def test_backup_manifest_lists_every_file_with_checksum(fake_world):
    result = make_backup(
        db_path=fake_world["db"],
        config_path=fake_world["cfg"],
        secrets_path=fake_world["env"],
        library_dir=fake_world["lib"],
        store=fake_world["store"],
    )
    m = result.manifest
    # Library + config + .env + library/book*.{epub,pdf} but NOT manifest.json itself
    keys = m.file_checksums.keys()
    assert "library.db" in keys
    assert "config.yaml" in keys
    assert ".env" in keys
    assert "library/book1.epub" in keys
    assert "library/book2.pdf" in keys
    assert MANIFEST_NAME not in keys

    # Checksums are sha256 (64 hex chars)
    for sha in m.file_checksums.values():
        assert len(sha) == 64
        int(sha, 16)  # round-trips as hex


@pytest.mark.skipif(not _is_zstd_available(), reason="zstd binary not present")
def test_backup_archive_is_valid_tar_zst(fake_world):
    """Read back the bundle from the store and verify it's a real
    tar.zst with the expected entries."""
    import zstandard

    result = make_backup(
        db_path=fake_world["db"],
        config_path=fake_world["cfg"],
        secrets_path=fake_world["env"],
        library_dir=fake_world["lib"],
        store=fake_world["store"],
    )
    archive_local = fake_world["tmp"] / "recovered.tar.zst"
    fake_world["store"].get(result.remote_key, archive_local)

    # Decompress + read tar
    with open(archive_local, "rb") as f:
        dctx = zstandard.ZstdDecompressor()
        with dctx.stream_reader(f) as reader, tarfile.open(fileobj=reader, mode="r|") as tf:
            names = tf.getnames()
    base = result.archive_path.name.removesuffix(".tar.zst")
    expected_prefix = base
    assert any(n == f"{expected_prefix}/library.db" for n in names)
    assert any(n == f"{expected_prefix}/config.yaml" for n in names)
    assert any(n == f"{expected_prefix}/{MANIFEST_NAME}" for n in names)


# ============ FEATURE-INTACT: optional inputs handled gracefully ============


@pytest.mark.skipif(not _is_zstd_available(), reason="zstd binary not present")
def test_secrets_optional(fake_world):
    """secrets_path=None must NOT fail — some users don't have .env."""
    result = make_backup(
        db_path=fake_world["db"],
        config_path=fake_world["cfg"],
        secrets_path=None,
        library_dir=fake_world["lib"],
        store=fake_world["store"],
    )
    assert ".env" not in result.manifest.file_checksums


@pytest.mark.skipif(not _is_zstd_available(), reason="zstd binary not present")
def test_library_optional(fake_world):
    """library_dir=None means "config + db only" — useful for fast
    config-only backups before risky changes."""
    result = make_backup(
        db_path=fake_world["db"],
        config_path=fake_world["cfg"],
        secrets_path=fake_world["env"],
        library_dir=None,
        store=fake_world["store"],
    )
    keys = result.manifest.file_checksums.keys()
    assert "library.db" in keys
    assert "config.yaml" in keys
    # No library/ entries
    assert not any(k.startswith("library/") for k in keys)


# ============ MANIFEST shape (independent of zstd) ============


def test_manifest_json_round_trips():
    m = BackupManifest(
        biblichor_version="0.1.0",
        python_version="3.12.3",
        platform="Linux-6.8",
        created_at_utc="2026-05-19T10:00:00Z",
        schema_version=1,
        file_checksums={"a": "ab" * 32},
        encrypted=False,
    )
    raw = m.to_json()
    parsed = json.loads(raw)
    assert parsed["biblichor_version"] == "0.1.0"
    assert parsed["schema_version"] == 1
    assert parsed["file_checksums"]["a"] == "ab" * 32
