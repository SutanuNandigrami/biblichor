"""Regression + feature-intact tests for `biblichor restore` (Phase 5b).

Strategy: produce a real backup via make_backup, then point restore
at it and verify the live targets get reconstructed faithfully.
Avoids mocking the archive format — the round-trip is the contract.
"""

from __future__ import annotations

import json
import shutil

import pytest

from endless_library.backup import (
    MANIFEST_NAME,
    make_backup,
)
from endless_library.restore import RestoreError, restore
from endless_library.storage.local import LocalStore


def _has_zstd():
    return shutil.which("zstd") is not None


@pytest.fixture
def world(tmp_path):
    """Build a working biblichor world: db + cfg + secrets + library.
    Then produce a backup of it into `<tmp>/store/`."""
    src = tmp_path / "src"
    src.mkdir()
    db = src / "library.db"
    import sqlite3

    with sqlite3.connect(str(db)) as conn:
        conn.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT, kind TEXT)")
        conn.execute("INSERT INTO books VALUES (1, 'টেস্ট বুক')")
        conn.execute("INSERT INTO events VALUES (1, '2020-01-01T00:00:00Z', 'create')")

    cfg = src / "config.yaml"
    cfg.write_text("general:\n  books_dir: /var/lib/biblichor\n")
    env = src / ".env"
    env.write_text("GMAIL_USER=demo@gmail.com\n")
    lib = src / "library"
    lib.mkdir()
    (lib / "book.epub").write_bytes(b"book content")

    store = LocalStore(tmp_path / "store")

    if not _has_zstd():
        pytest.skip("zstd required for backup/restore round-trip")
    result = make_backup(
        db_path=db,
        config_path=cfg,
        secrets_path=env,
        library_dir=lib,
        store=store,
        age_recipient=None,
    )
    return {
        "src": src,
        "store": store,
        "backup_key": result.remote_key,
        "tmp": tmp_path,
        "manifest": result.manifest,
    }


# ============ FEATURE-INTACT: end-to-end round-trip ============


def test_restore_recreates_db_config_secrets_library(world, tmp_path):
    archive_local = tmp_path / "downloaded.tar.zst"
    world["store"].get(world["backup_key"], archive_local)

    new_world = tmp_path / "new"
    new_world.mkdir()
    result = restore(
        archive_path=archive_local,
        db_target=new_world / "library.db",
        config_target=new_world / "config.yaml",
        secrets_target=new_world / ".env",
        library_target=new_world / "library",
    )
    assert result.db_restored
    assert result.config_restored
    assert result.secrets_restored
    assert result.library_restored

    # Concrete artifacts in place
    import sqlite3

    with sqlite3.connect(str(new_world / "library.db")) as conn:
        rows = conn.execute("SELECT id, title FROM books").fetchall()
    assert rows == [(1, "টেস্ট বুক")]

    assert (new_world / "config.yaml").read_text().startswith("general:")
    assert "GMAIL_USER=demo@gmail.com" in (new_world / ".env").read_text()
    assert (new_world / "library" / "book.epub").read_bytes() == b"book content"


def test_restore_returns_files_validated_count(world, tmp_path):
    archive_local = tmp_path / "downloaded.tar.zst"
    world["store"].get(world["backup_key"], archive_local)
    new_world = tmp_path / "new"
    new_world.mkdir()
    result = restore(
        archive_path=archive_local,
        db_target=new_world / "library.db",
        config_target=new_world / "config.yaml",
        secrets_target=new_world / ".env",
        library_target=new_world / "library",
    )
    assert result.files_validated == len(world["manifest"].file_checksums)


def test_restore_preserves_existing_targets_as_bak(world, tmp_path):
    """If live targets exist, restore renames them to .bak-<ts> so a
    botched restore is recoverable."""
    archive_local = tmp_path / "downloaded.tar.zst"
    world["store"].get(world["backup_key"], archive_local)

    new_world = tmp_path / "new"
    new_world.mkdir()
    (new_world / "config.yaml").write_text("OLD CONFIG")

    restore(
        archive_path=archive_local,
        db_target=new_world / "library.db",
        config_target=new_world / "config.yaml",
        secrets_target=None,
        library_target=None,
    )
    # Original config moved aside
    backups = list(new_world.glob("config.yaml.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "OLD CONFIG"


# ============ REGRESSION: corruption + version protection ============


def test_restore_rejects_missing_archive(tmp_path):
    with pytest.raises(RestoreError, match="archive not found"):
        restore(
            archive_path=tmp_path / "nope.tar.zst",
            db_target=tmp_path / "x.db",
            config_target=tmp_path / "x.yaml",
            secrets_target=None,
            library_target=None,
        )


def test_restore_rejects_schema_version_mismatch(world, tmp_path):
    """If we tamper with the manifest to claim a different schema
    version, restore must abort BEFORE touching live data."""
    # Extract, edit manifest, repack
    archive_local = tmp_path / "downloaded.tar.zst"
    world["store"].get(world["backup_key"], archive_local)

    work = tmp_path / "work"
    work.mkdir()
    import subprocess

    proc = subprocess.run(["zstd", "-d", "-c", str(archive_local)], capture_output=True)
    tar_blob = proc.stdout
    import io
    import tarfile as _tar

    members = []
    with _tar.open(fileobj=io.BytesIO(tar_blob), mode="r|") as tf:
        for m in tf:
            f = tf.extractfile(m)
            members.append((m, f.read() if f else None))

    # Locate + tamper with the manifest
    for i, (m, data) in enumerate(members):
        if m.name.endswith(MANIFEST_NAME):
            manifest = json.loads(data)
            manifest["schema_version"] = 9999
            new_data = json.dumps(manifest, indent=2, sort_keys=True).encode()
            m.size = len(new_data)
            members[i] = (m, new_data)
            break

    # Repack as tar.zst
    bad_archive = tmp_path / "bad.tar.zst"
    buf = io.BytesIO()
    with _tar.open(fileobj=buf, mode="w") as tf:
        for m, data in members:
            if data is None:
                continue
            tf.addfile(m, io.BytesIO(data))
    raw_tar = buf.getvalue()
    subprocess.run(
        ["zstd", "-q", "-T0", "-19", "-o", str(bad_archive), "-"],
        input=raw_tar,
        check=True,
    )

    new_world = tmp_path / "new"
    new_world.mkdir()
    with pytest.raises(RestoreError, match="schema_version=9999"):
        restore(
            archive_path=bad_archive,
            db_target=new_world / "library.db",
            config_target=new_world / "config.yaml",
            secrets_target=None,
            library_target=None,
        )

    # Live targets were not touched
    assert not (new_world / "library.db").exists()
    assert not (new_world / "config.yaml").exists()


def test_restore_rejects_when_live_newer(world, tmp_path):
    """Live DB with a newer event than the backup's created_at must
    block restore unless --force."""
    archive_local = tmp_path / "downloaded.tar.zst"
    world["store"].get(world["backup_key"], archive_local)

    new_world = tmp_path / "new"
    new_world.mkdir()
    # Make a "live" db whose latest event is in 2099
    import sqlite3

    with sqlite3.connect(str(new_world / "library.db")) as conn:
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT, kind TEXT)")
        conn.execute("INSERT INTO events VALUES (1, '2099-01-01T00:00:00Z', 'future')")

    with pytest.raises(RestoreError, match="live DB has events newer"):
        restore(
            archive_path=archive_local,
            db_target=new_world / "library.db",
            config_target=new_world / "config.yaml",
            secrets_target=None,
            library_target=None,
            force=False,
        )


def test_restore_force_overrides_newer_live(world, tmp_path):
    """--force bypasses the newer-live guard."""
    archive_local = tmp_path / "downloaded.tar.zst"
    world["store"].get(world["backup_key"], archive_local)

    new_world = tmp_path / "new"
    new_world.mkdir()
    import sqlite3

    with sqlite3.connect(str(new_world / "library.db")) as conn:
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT, kind TEXT)")
        conn.execute("INSERT INTO events VALUES (1, '2099-01-01T00:00:00Z', 'future')")

    result = restore(
        archive_path=archive_local,
        db_target=new_world / "library.db",
        config_target=new_world / "config.yaml",
        secrets_target=None,
        library_target=None,
        force=True,
    )
    assert result.db_restored
    # And the future DB is preserved as .bak
    assert any(p.name.startswith("library.db.bak-") for p in new_world.iterdir())


def test_restore_rejects_age_without_identity(tmp_path):
    """Encrypted .age archives need an identity file. Missing => clear error."""
    enc = tmp_path / "fake.tar.zst.age"
    enc.write_bytes(b"fake age stream")
    with pytest.raises(RestoreError, match="no --age-identity"):
        restore(
            archive_path=enc,
            db_target=tmp_path / "x.db",
            config_target=tmp_path / "x.yaml",
            secrets_target=None,
            library_target=None,
        )
