"""Regression + feature-intact tests for RcloneStore + HybridStore.

RcloneStore tests use a mock `subprocess.run` so we don't need the
actual rclone binary or a configured remote. HybridStore tests
combine two LocalStores in tmp_path so the cross-backend semantics
(mirror vs scheduled, fallback reads, etc.) get exercised end-to-end.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from endless_library.storage.base import KeyNotFound, StorageError, Store
from endless_library.storage.hybrid import HybridStore
from endless_library.storage.local import LocalStore
from endless_library.storage.rclone import RcloneStore


# ============ RcloneStore (mocked subprocess) ============


class _FakeProc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def rclone(monkeypatch):
    """RcloneStore with the binary stubbed to exist on PATH."""
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/rclone")
    return RcloneStore(remote="gdrive", bucket_path="biblichor")


def test_rclone_constructor_fails_when_binary_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: None)
    with pytest.raises(StorageError, match="not on PATH"):
        RcloneStore(remote="gdrive")


def test_rclone_satisfies_store_protocol(rclone):
    assert isinstance(rclone, Store)


def test_rclone_remote_path_includes_bucket_prefix(rclone):
    assert rclone._remote_path("books/x.epub") == "gdrive:biblichor/books/x.epub"


def test_rclone_remote_path_handles_empty_prefix(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/rclone")
    r = RcloneStore(remote="s3")
    assert r._remote_path("books/x.epub") == "s3:books/x.epub"


def test_rclone_put_calls_copyto(rclone, tmp_path):
    src = tmp_path / "x.bin"
    src.write_bytes(b"data")
    calls = []

    def fake_run(self, args):
        calls.append(args)
        return _FakeProc(0)

    with patch.object(RcloneStore, "_run", fake_run):
        rclone.put(src, "books/x.bin")
    assert calls[0] == ["copyto", str(src), "gdrive:biblichor/books/x.bin"]


def test_rclone_put_raises_on_nonzero_exit(rclone, tmp_path):
    src = tmp_path / "x.bin"
    src.write_bytes(b"data")

    with patch.object(RcloneStore, "_run", lambda self, args: _FakeProc(1, stderr="drive permission denied")):
        with pytest.raises(StorageError, match="permission denied"):
            rclone.put(src, "x.bin")


def test_rclone_get_raises_key_not_found_when_missing(rclone, tmp_path):
    # exists() probes via lsf; we return empty stdout + zero exit
    with patch.object(RcloneStore, "_run", lambda self, args: _FakeProc(0, stdout="")):
        with pytest.raises(KeyNotFound):
            rclone.get("absent.bin", tmp_path / "out.bin")


def test_rclone_get_calls_copyto_when_present(rclone, tmp_path):
    calls = []

    def fake_run(self, args):
        calls.append(args)
        # First call is the existence probe; second is the actual download
        if args[0] == "lsf":
            return _FakeProc(0, stdout="x.bin\n")
        return _FakeProc(0)

    with patch.object(RcloneStore, "_run", fake_run):
        rclone.get("books/x.bin", tmp_path / "out.bin")
    assert calls[-1][0] == "copyto"
    assert calls[-1][1] == "gdrive:biblichor/books/x.bin"


def test_rclone_exists_true_when_lsf_returns_line(rclone):
    with patch.object(RcloneStore, "_run", lambda self, args: _FakeProc(0, stdout="x.bin\n")):
        assert rclone.exists("x.bin") is True


def test_rclone_exists_false_on_empty_output(rclone):
    with patch.object(RcloneStore, "_run", lambda self, args: _FakeProc(0, stdout="")):
        assert rclone.exists("x.bin") is False


def test_rclone_exists_false_on_nonzero_exit(rclone):
    """Some backends (S3 path-style) return non-zero for non-existent
    keys; we still must return False, not raise."""
    with patch.object(RcloneStore, "_run", lambda self, args: _FakeProc(1, stderr="404")):
        assert rclone.exists("x.bin") is False


def test_rclone_delete_idempotent_on_missing(rclone):
    """rclone-side "object not found" must NOT raise."""
    with patch.object(RcloneStore, "_run", lambda self, args: _FakeProc(3, stderr="Object not found")):
        rclone.delete("x.bin")  # must not raise


def test_rclone_list_yields_keys_with_prefix(rclone):
    with patch.object(
        RcloneStore,
        "_run",
        lambda self, args: _FakeProc(0, stdout="a.epub\nb.epub\nsub/c.epub\n"),
    ):
        keys = list(rclone.list("books/"))
    # With prefix, lsf is called on the prefix scope; output is relative
    assert "books/a.epub" in keys
    assert "books/sub/c.epub" in keys


# ============ HybridStore (real LocalStores) ============


@pytest.fixture
def hybrid_mirror(tmp_path):
    p = LocalStore(tmp_path / "primary")
    b = LocalStore(tmp_path / "backup")
    return HybridStore(p, b, mode="mirror"), p, b


@pytest.fixture
def hybrid_scheduled(tmp_path):
    p = LocalStore(tmp_path / "primary2")
    b = LocalStore(tmp_path / "backup2")
    return HybridStore(p, b, mode="scheduled"), p, b


@pytest.fixture
def sample_file(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"sample bytes")
    return f


def test_hybrid_satisfies_store_protocol(hybrid_mirror):
    h, _, _ = hybrid_mirror
    assert isinstance(h, Store)


def test_hybrid_mirror_writes_to_both(hybrid_mirror, sample_file):
    h, p, b = hybrid_mirror
    h.put(sample_file, "x.bin")
    assert p.exists("x.bin")
    assert b.exists("x.bin")


def test_hybrid_scheduled_writes_only_to_primary(hybrid_scheduled, sample_file):
    h, p, b = hybrid_scheduled
    h.put(sample_file, "x.bin")
    assert p.exists("x.bin")
    assert not b.exists("x.bin")


def test_hybrid_mirror_swallows_backup_failure(hybrid_mirror, sample_file, monkeypatch):
    """Backup write failing must NOT fail the primary write."""
    h, p, b = hybrid_mirror

    def boom(local_path, remote_key):
        raise StorageError("simulated backup outage")

    monkeypatch.setattr(b, "put", boom)
    h.put(sample_file, "x.bin")  # must not raise
    assert p.exists("x.bin")


def test_hybrid_mirror_propagates_primary_failure(hybrid_mirror, sample_file, monkeypatch):
    """Primary write failure IS authoritative — propagate."""
    h, p, b = hybrid_mirror

    def boom(local_path, remote_key):
        raise StorageError("primary outage")

    monkeypatch.setattr(p, "put", boom)
    with pytest.raises(StorageError, match="primary outage"):
        h.put(sample_file, "x.bin")


def test_hybrid_get_prefers_primary(hybrid_mirror, sample_file, tmp_path):
    h, p, b = hybrid_mirror
    p.put(sample_file, "x.bin")  # primary only — backup empty
    out = tmp_path / "out.bin"
    h.get("x.bin", out)
    assert out.read_bytes() == b"sample bytes"


def test_hybrid_get_falls_back_to_backup(hybrid_mirror, sample_file, tmp_path):
    """If primary is missing the key but backup has it, return backup."""
    h, p, b = hybrid_mirror
    b.put(sample_file, "x.bin")  # backup only
    out = tmp_path / "out.bin"
    h.get("x.bin", out)
    assert out.read_bytes() == b"sample bytes"


def test_hybrid_get_propagates_backup_missing(hybrid_mirror, tmp_path):
    """Neither side has it -> KeyNotFound."""
    h, p, b = hybrid_mirror
    with pytest.raises(KeyNotFound):
        h.get("absent.bin", tmp_path / "out.bin")


def test_hybrid_exists_true_if_either_side_has_it(hybrid_mirror, sample_file):
    h, p, b = hybrid_mirror
    b.put(sample_file, "only-on-backup.bin")
    assert h.exists("only-on-backup.bin")
    p.put(sample_file, "only-on-primary.bin")
    assert h.exists("only-on-primary.bin")


def test_hybrid_delete_removes_from_both(hybrid_mirror, sample_file):
    h, p, b = hybrid_mirror
    h.put(sample_file, "doomed.bin")
    h.delete("doomed.bin")
    assert not p.exists("doomed.bin")
    assert not b.exists("doomed.bin")


def test_hybrid_list_unions_and_sorts(hybrid_mirror, sample_file):
    h, p, b = hybrid_mirror
    p.put(sample_file, "only-p.bin")
    b.put(sample_file, "only-b.bin")
    p.put(sample_file, "both.bin")
    b.put(sample_file, "both.bin")
    keys = list(h.list())
    assert keys == sorted(set(keys))  # sorted + dedup
    for k in ("only-p.bin", "only-b.bin", "both.bin"):
        assert k in keys
