"""Regression + feature-intact tests for the storage Protocol + LocalStore.

The interface is the contract every backend (rclone, hybrid) must
satisfy. These tests pin the contract using LocalStore as the
reference implementation. Phase 4b's tests will repeat the same
contract checks against RcloneStore (mocked) and HybridStore.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from endless_library.storage.base import KeyNotFound, StorageError, Store
from endless_library.storage.local import LocalStore


@pytest.fixture
def store(tmp_path: Path) -> LocalStore:
    return LocalStore(tmp_path / "blob-root")


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    p = tmp_path / "sample.bin"
    p.write_bytes(b"hello biblichor")
    return p


# ============ INTERFACE: implements the Store Protocol ============


def test_localstore_satisfies_store_protocol(store):
    """Runtime check the structural subtype matches. If anyone adds a
    method to the Protocol they MUST add it to every backend."""
    assert isinstance(store, Store)


# ============ FEATURE-INTACT: round-trip put/get ============


def test_put_then_get_round_trips(store, sample_file, tmp_path):
    store.put(sample_file, "books/x.epub")
    out = tmp_path / "recovered.epub"
    store.get("books/x.epub", out)
    assert out.read_bytes() == b"hello biblichor"


def test_put_creates_intermediate_directories(store, sample_file):
    store.put(sample_file, "deep/nested/dir/file.bin")
    assert store.exists("deep/nested/dir/file.bin")


def test_put_overwrites_existing_key(store, sample_file, tmp_path):
    store.put(sample_file, "x.bin")
    new = tmp_path / "new.bin"
    new.write_bytes(b"replacement")
    store.put(new, "x.bin")
    recovered = tmp_path / "out.bin"
    store.get("x.bin", recovered)
    assert recovered.read_bytes() == b"replacement"


def test_exists_returns_false_for_missing(store):
    assert store.exists("nope.epub") is False


def test_exists_returns_true_after_put(store, sample_file):
    store.put(sample_file, "exists.bin")
    assert store.exists("exists.bin") is True


def test_delete_is_idempotent_on_missing_key(store):
    # Must NOT raise — important for cleanup loops
    store.delete("never-existed.bin")


def test_delete_removes_existing_key(store, sample_file):
    store.put(sample_file, "doomed.bin")
    assert store.exists("doomed.bin")
    store.delete("doomed.bin")
    assert not store.exists("doomed.bin")


def test_list_returns_keys_in_lex_order(store, sample_file):
    for k in ["z.bin", "a/sub.bin", "a/other.bin", "m/mid.bin"]:
        store.put(sample_file, k)
    keys = list(store.list())
    assert keys == sorted(keys)
    assert "a/other.bin" in keys
    assert "a/sub.bin" in keys
    assert "m/mid.bin" in keys
    assert "z.bin" in keys


def test_list_with_prefix(store, sample_file):
    for k in ["books/a.epub", "books/b.epub", "backups/c.tar"]:
        store.put(sample_file, k)
    books = list(store.list("books/"))
    assert books == ["books/a.epub", "books/b.epub"]


# ============ REGRESSION: error handling contract ============


def test_get_missing_key_raises_key_not_found(store, tmp_path):
    with pytest.raises(KeyNotFound):
        store.get("absent.bin", tmp_path / "out.bin")


def test_put_missing_source_raises_storage_error(store):
    with pytest.raises(StorageError, match="source not found"):
        store.put(Path("/does/not/exist.bin"), "x.bin")


def test_path_traversal_rejected(store, sample_file):
    """Storage keys must NEVER let the caller escape the root via
    `..` segments. LocalStore raises StorageError; on other backends
    the resolution is similar."""
    with pytest.raises(StorageError, match="escapes root"):
        store.put(sample_file, "../../etc/passwd")


def test_exists_handles_invalid_key_shape(store):
    """exists() must not raise for an out-of-bounds key — return False."""
    assert store.exists("../../etc/passwd") is False


# ============ FEATURE-INTACT: root directory created lazily ============


def test_root_created_on_init(tmp_path):
    new_root = tmp_path / "fresh-root-that-doesnt-exist"
    assert not new_root.exists()
    LocalStore(new_root)
    assert new_root.is_dir()


def test_localstore_name_is_local(store):
    """UI / logging key — pin the conventional name."""
    assert store.name == "local"
