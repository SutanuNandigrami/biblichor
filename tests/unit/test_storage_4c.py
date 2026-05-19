"""Regression + feature-intact tests for the storage factory + migrate.

These pin two contracts: (1) build_store reads the StorageCfg
correctly and returns the right Store subclass per backend; (2)
migrate_all is idempotent (skipping already-present keys) and
returns a structured result the CLI can print.
"""

from __future__ import annotations

import pytest

from endless_library.config import Config, StorageCfg
from endless_library.storage.base import StorageError
from endless_library.storage.factory import build_store
from endless_library.storage.hybrid import HybridStore
from endless_library.storage.local import LocalStore
from endless_library.storage.migrate import migrate_all


@pytest.fixture
def sample(tmp_path):
    f = tmp_path / "sample.bin"
    f.write_bytes(b"x" * 100)
    return f


# ============ StorageCfg defaults ============


def test_storage_cfg_defaults_to_local():
    cfg = StorageCfg()
    assert cfg.backend == "local"
    assert cfg.local_root == ""  # fallback to general.books_dir
    assert cfg.rclone_remote == ""
    assert cfg.rclone_bucket_path == ""
    assert cfg.hybrid_mode == "mirror"


def test_config_now_includes_storage():
    """Config dataclass exposes the storage section so YAML round-trips."""
    c = Config()
    assert isinstance(c.storage, StorageCfg)


# ============ build_store ============


def test_build_store_default_returns_localstore(tmp_path):
    cfg = StorageCfg(backend="local", local_root=str(tmp_path / "books"))
    s = build_store(cfg, data_root=tmp_path)
    assert isinstance(s, LocalStore)
    assert s.name == "local"


def test_build_store_local_falls_back_to_data_root(tmp_path):
    """When local_root is empty, LocalStore lands files at data_root."""
    cfg = StorageCfg(backend="local", local_root="")
    s = build_store(cfg, data_root=tmp_path)
    assert isinstance(s, LocalStore)
    # First put should land under tmp_path
    sample = tmp_path / "src.bin"
    sample.write_bytes(b"x")
    s.put(sample, "books/x.bin")
    assert (tmp_path / "books" / "x.bin").exists()


def test_build_store_rclone_requires_remote(tmp_path):
    cfg = StorageCfg(backend="rclone")  # no rclone_remote
    with pytest.raises(ValueError, match="rclone_remote is required"):
        build_store(cfg, data_root=tmp_path)


def test_build_store_rclone_uses_shutil_which(tmp_path, monkeypatch):
    """If rclone binary missing, the underlying Store constructor raises
    so misconfiguration is caught at startup, not on first I/O."""
    monkeypatch.setattr("shutil.which", lambda x: None)
    cfg = StorageCfg(backend="rclone", rclone_remote="gdrive")
    with pytest.raises(StorageError, match="not on PATH"):
        build_store(cfg, data_root=tmp_path)


def test_build_store_hybrid_returns_hybridstore(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/rclone")
    cfg = StorageCfg(
        backend="hybrid",
        rclone_remote="gdrive",
        rclone_bucket_path="biblichor",
        hybrid_mode="mirror",
    )
    s = build_store(cfg, data_root=tmp_path)
    assert isinstance(s, HybridStore)
    assert s.mode == "mirror"


def test_build_store_rejects_unknown_backend(tmp_path):
    cfg = StorageCfg(backend="dropbox-via-magic")
    with pytest.raises(ValueError, match="unknown storage backend"):
        build_store(cfg, data_root=tmp_path)


def test_build_store_rejects_invalid_hybrid_mode(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/rclone")
    cfg = StorageCfg(
        backend="hybrid", rclone_remote="gdrive", hybrid_mode="frenzied"
    )
    with pytest.raises(ValueError, match="hybrid_mode must be"):
        build_store(cfg, data_root=tmp_path)


# ============ migrate_all ============


@pytest.fixture
def two_stores(tmp_path):
    src = LocalStore(tmp_path / "src")
    dst = LocalStore(tmp_path / "dst")
    return src, dst


def test_migrate_copies_all_keys(two_stores, sample):
    src, dst = two_stores
    for k in ["a.bin", "sub/b.bin", "z.bin"]:
        src.put(sample, k)
    result = migrate_all(src, dst)
    assert result.total == 3
    assert result.copied == 3
    assert result.skipped_existing == 0
    assert result.failed == 0
    for k in ["a.bin", "sub/b.bin", "z.bin"]:
        assert dst.exists(k)


def test_migrate_is_idempotent(two_stores, sample):
    """Second run finds all keys present on dst and skips them."""
    src, dst = two_stores
    for k in ["a.bin", "b.bin"]:
        src.put(sample, k)
    first = migrate_all(src, dst)
    assert first.copied == 2
    second = migrate_all(src, dst)
    assert second.total == 2
    assert second.copied == 0
    assert second.skipped_existing == 2


def test_migrate_overwrite_forces_recopy(two_stores, sample, tmp_path):
    src, dst = two_stores
    src.put(sample, "x.bin")
    migrate_all(src, dst)
    # Modify on src
    src.put(_with_content(tmp_path, b"NEW DATA"), "x.bin")
    # Without overwrite: dst keeps old bytes
    migrate_all(src, dst, overwrite=False)
    out = tmp_path / "out.bin"
    dst.get("x.bin", out)
    assert out.read_bytes() == b"x" * 100
    # With overwrite: dst gets new bytes
    result = migrate_all(src, dst, overwrite=True)
    assert result.copied == 1
    dst.get("x.bin", out)
    assert out.read_bytes() == b"NEW DATA"


def test_migrate_with_prefix(two_stores, sample):
    src, dst = two_stores
    src.put(sample, "books/a.bin")
    src.put(sample, "books/b.bin")
    src.put(sample, "backups/c.bin")
    result = migrate_all(src, dst, prefix="books/")
    assert result.total == 2
    assert dst.exists("books/a.bin") and dst.exists("books/b.bin")
    assert not dst.exists("backups/c.bin")


def test_migrate_records_per_key_failures(two_stores, sample, monkeypatch):
    """One bad key shouldn't abort the rest of the run; the failure
    surfaces in result.errors."""
    src, dst = two_stores
    for k in ["a.bin", "b.bin"]:
        src.put(sample, k)
    real_put = dst.put

    def flaky_put(local_path, remote_key):
        if remote_key == "b.bin":
            raise StorageError("simulated upload failure")
        return real_put(local_path, remote_key)

    monkeypatch.setattr(dst, "put", flaky_put)
    result = migrate_all(src, dst)
    assert result.total == 2
    assert result.copied == 1
    assert result.failed == 1
    assert len(result.errors) == 1
    assert result.errors[0][0] == "b.bin"


def test_migrate_progress_callback_invoked(two_stores, sample):
    src, dst = two_stores
    for k in ["a.bin", "b.bin", "c.bin"]:
        src.put(sample, k)
    seen: list[tuple[str, int, int]] = []
    migrate_all(src, dst, on_progress=lambda k, n, t: seen.append((k, n, t)))
    assert len(seen) == 3
    assert [t for _, _, t in seen] == [3, 3, 3]


def _with_content(tmp_path, content: bytes):
    p = tmp_path / "src_modified.bin"
    p.write_bytes(content)
    return p
