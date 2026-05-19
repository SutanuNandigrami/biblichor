"""Phase 6g — backup + restore handle Postgres dump and BookOrbit data."""

from __future__ import annotations

import shutil
import subprocess
import tarfile

import pytest
import zstandard

from endless_library.backup import BackupError, make_backup
from endless_library.restore import restore
from endless_library.storage.local import LocalStore


def _has_zstd():
    return shutil.which("zstd") is not None


def _seed_world(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    import sqlite3

    db = src / "library.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute("CREATE TABLE books (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT)")
    cfg = src / "config.yaml"
    cfg.write_text("general: {}\n")
    bo_data = src / "bookorbit-data"
    bo_data.mkdir()
    (bo_data / "covers").mkdir()
    (bo_data / "covers" / "abc.jpg").write_bytes(b"\xff\xd8\xff" * 10)
    (bo_data / "book-bucket").mkdir()
    (bo_data / "book-bucket" / "staging.txt").write_text("upload staging")
    return {
        "src": src,
        "db": db,
        "cfg": cfg,
        "bo_data": bo_data,
        "store": LocalStore(tmp_path / "store"),
    }


# ============ REGRESSION: postgres + bookorbit-data live in the bundle ============


@pytest.mark.skipif(not _has_zstd(), reason="zstd required")
def test_postgres_dump_captured_into_archive(tmp_path, monkeypatch):
    """pg_dump's stdout is captured into postgres.sql inside the bundle."""
    import endless_library.backup as backup_mod

    w = _seed_world(tmp_path)

    real_run = subprocess.run

    def fake_run(cmd, **kw):
        # Only intercept the pg_dump call (which we identified as
        # subprocess.run in the backup module); let other callers
        # (platform module's uname probe, etc.) hit real subprocess.run.
        if isinstance(cmd, list) and cmd and "pg_dump" in cmd:

            class _Proc:
                returncode = 0
                stdout = b"-- PostgreSQL database dump\n-- bookorbit\n"
                stderr = b""

            return _Proc()
        return real_run(cmd, **kw)

    monkeypatch.setattr(backup_mod.subprocess, "run", fake_run)

    result = make_backup(
        db_path=w["db"],
        config_path=w["cfg"],
        secrets_path=None,
        library_dir=None,
        store=w["store"],
        postgres_dump_cmd=[
            "docker",
            "compose",
            "exec",
            "-T",
            "bookorbit-db",
            "pg_dump",
            "-U",
            "bookorbit",
            "bookorbit",
        ],
    )

    # The manifest lists postgres.sql
    assert "postgres.sql" in result.manifest.file_checksums

    # And extracting the bundle confirms it
    archive_local = tmp_path / "got.tar.zst"
    w["store"].get(result.remote_key, archive_local)
    found = False
    with open(archive_local, "rb") as f:
        dctx = zstandard.ZstdDecompressor()
        with dctx.stream_reader(f) as reader, tarfile.open(fileobj=reader, mode="r|") as tf:
            for m in tf:
                if m.name.endswith("/postgres.sql"):
                    extracted = tf.extractfile(m).read()
                    assert b"PostgreSQL database dump" in extracted
                    found = True
                    break
    assert found


@pytest.mark.skipif(not _has_zstd(), reason="zstd required")
def test_pg_dump_failure_aborts_backup(tmp_path, monkeypatch):
    """If pg_dump exits non-zero, we must NOT ship a partial bundle."""
    import endless_library.backup as backup_mod

    w = _seed_world(tmp_path)

    real_run = subprocess.run

    def fake_run(cmd, **kw):
        if isinstance(cmd, list) and cmd and "pg_dump" in cmd:

            class _Proc:
                returncode = 1
                stdout = b""
                stderr = b"could not connect to db"

            return _Proc()
        return real_run(cmd, **kw)

    monkeypatch.setattr(backup_mod.subprocess, "run", fake_run)

    with pytest.raises(BackupError, match="pg_dump exit 1"):
        make_backup(
            db_path=w["db"],
            config_path=w["cfg"],
            secrets_path=None,
            library_dir=None,
            store=w["store"],
            postgres_dump_cmd=["pg_dump", "-U", "x", "y"],
        )


@pytest.mark.skipif(not _has_zstd(), reason="zstd required")
def test_bookorbit_data_dir_included(tmp_path):
    """If bookorbit_data_dir is given, the bundle includes its contents."""
    w = _seed_world(tmp_path)
    result = make_backup(
        db_path=w["db"],
        config_path=w["cfg"],
        secrets_path=None,
        library_dir=None,
        store=w["store"],
        bookorbit_data_dir=w["bo_data"],
    )
    keys = result.manifest.file_checksums.keys()
    assert "bookorbit-data/covers/abc.jpg" in keys
    assert "bookorbit-data/book-bucket/staging.txt" in keys


@pytest.mark.skipif(not _has_zstd(), reason="zstd required")
def test_neither_postgres_nor_bookorbit_skips_cleanly(tmp_path):
    """Existing biblichor backups (Phase 5a default) keep working —
    bundle contains exactly what it always did."""
    w = _seed_world(tmp_path)
    result = make_backup(
        db_path=w["db"],
        config_path=w["cfg"],
        secrets_path=None,
        library_dir=None,
        store=w["store"],
        postgres_dump_cmd=None,
        bookorbit_data_dir=None,
    )
    keys = set(result.manifest.file_checksums.keys())
    assert "postgres.sql" not in keys
    assert not any(k.startswith("bookorbit-data/") for k in keys)


# ============ RESTORE: stages postgres + bookorbit-data ============


@pytest.mark.skipif(not _has_zstd(), reason="zstd required")
def test_restore_stages_postgres_dump_at_target(tmp_path, monkeypatch):
    """Restore writes postgres.sql to the operator-chosen path; does
    NOT auto-load. RestoreResult.postgres_dump_staged_path points at it."""
    import endless_library.backup as backup_mod

    w = _seed_world(tmp_path)
    real_run = subprocess.run

    def fake_run(cmd, **kw):
        if isinstance(cmd, list) and cmd and "pg_dump" in cmd:

            class _Proc:
                returncode = 0
                stdout = b"-- PostgreSQL dump\nSELECT 1;\n"
                stderr = b""

            return _Proc()
        return real_run(cmd, **kw)

    monkeypatch.setattr(backup_mod.subprocess, "run", fake_run)

    result = make_backup(
        db_path=w["db"],
        config_path=w["cfg"],
        secrets_path=None,
        library_dir=None,
        store=w["store"],
        postgres_dump_cmd=["pg_dump"],
        bookorbit_data_dir=w["bo_data"],
    )

    # Pull + restore
    archive_local = tmp_path / "got.tar.zst"
    w["store"].get(result.remote_key, archive_local)
    new = tmp_path / "new"
    new.mkdir()
    rr = restore(
        archive_path=archive_local,
        db_target=new / "library.db",
        config_target=new / "config.yaml",
        secrets_target=None,
        library_target=None,
        postgres_dump_target=new / "postgres.sql",
        bookorbit_data_target=new / "bookorbit-data",
    )
    assert rr.postgres_dump_staged_path == new / "postgres.sql"
    assert (new / "postgres.sql").read_bytes().startswith(b"-- PostgreSQL dump")
    assert rr.bookorbit_data_restored is True
    assert (new / "bookorbit-data" / "covers" / "abc.jpg").exists()


@pytest.mark.skipif(not _has_zstd(), reason="zstd required")
def test_restore_handles_archive_with_no_postgres_or_bookorbit(tmp_path):
    """Backwards compat: restoring a pre-Phase-6g bundle (no postgres,
    no bookorbit-data) still works and just reports them as absent."""
    w = _seed_world(tmp_path)
    result = make_backup(
        db_path=w["db"],
        config_path=w["cfg"],
        secrets_path=None,
        library_dir=None,
        store=w["store"],
    )
    archive_local = tmp_path / "got.tar.zst"
    w["store"].get(result.remote_key, archive_local)
    new = tmp_path / "new"
    new.mkdir()
    rr = restore(
        archive_path=archive_local,
        db_target=new / "library.db",
        config_target=new / "config.yaml",
        secrets_target=None,
        library_target=None,
        postgres_dump_target=new / "postgres.sql",
        bookorbit_data_target=new / "bookorbit-data",
    )
    assert rr.postgres_dump_staged_path is None
    assert rr.bookorbit_data_restored is False


@pytest.mark.skipif(not _has_zstd(), reason="zstd required")
def test_restore_preserves_existing_bookorbit_data_as_bak(tmp_path):
    """Same .bak-<ts> guard as for DB/config — botched restore must
    leave a recoverable artifact."""
    w = _seed_world(tmp_path)
    result = make_backup(
        db_path=w["db"],
        config_path=w["cfg"],
        secrets_path=None,
        library_dir=None,
        store=w["store"],
        bookorbit_data_dir=w["bo_data"],
    )
    archive_local = tmp_path / "got.tar.zst"
    w["store"].get(result.remote_key, archive_local)
    new = tmp_path / "new"
    new.mkdir()
    # Pre-existing bookorbit-data dir
    existing = new / "bookorbit-data"
    existing.mkdir()
    (existing / "old-cover.jpg").write_bytes(b"OLD")

    restore(
        archive_path=archive_local,
        db_target=new / "library.db",
        config_target=new / "config.yaml",
        secrets_target=None,
        library_target=None,
        postgres_dump_target=None,
        bookorbit_data_target=existing,
    )
    # New dir landed
    assert (existing / "covers" / "abc.jpg").exists()
    # Old preserved as .bak-<ts>
    backups = list(new.glob("bookorbit-data.bak-*"))
    assert len(backups) == 1
    assert (backups[0] / "old-cover.jpg").read_bytes() == b"OLD"
