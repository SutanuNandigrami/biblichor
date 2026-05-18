"""Local filesystem backend.

This is biblichor's default storage; the pipeline can stay
backend-agnostic while behaving exactly as before. Keys are stored
as files under a root directory; subdirectories are created as
needed.

Used as both the primary store (when storage.backend=local) and the
"local cache" side of a hybrid setup.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Iterator

from endless_library.storage.base import KeyNotFound, StorageError

log = logging.getLogger(__name__)


class LocalStore:
    name = "local"

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _key_path(self, remote_key: str) -> Path:
        # Reject keys that would traverse out of root
        key = remote_key.lstrip("/")
        # Normalize and prevent ../ escape
        target = (self.root / key).resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError as e:
            raise StorageError(f"key escapes root: {remote_key!r}") from e
        return target

    def put(self, local_path: Path, remote_key: str) -> None:
        local_path = Path(local_path)
        if not local_path.exists():
            raise StorageError(f"source not found: {local_path}")
        dst = self._key_path(remote_key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        # atomic on the same filesystem; degrades to copy+replace across FS
        try:
            shutil.copy2(local_path, dst)
        except OSError as e:
            raise StorageError(f"put failed: {e}") from e

    def get(self, remote_key: str, local_path: Path) -> None:
        src = self._key_path(remote_key)
        if not src.exists():
            raise KeyNotFound(remote_key)
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, local_path)
        except OSError as e:
            raise StorageError(f"get failed: {e}") from e

    def exists(self, remote_key: str) -> bool:
        try:
            return self._key_path(remote_key).exists()
        except StorageError:
            # Invalid key shape → treat as nonexistent rather than crashing
            return False

    def delete(self, remote_key: str) -> None:
        try:
            target = self._key_path(remote_key)
        except StorageError:
            # Invalid key shape: nothing to do
            return
        try:
            target.unlink(missing_ok=True)
        except IsADirectoryError:
            shutil.rmtree(target, ignore_errors=True)
        except OSError as e:
            raise StorageError(f"delete failed: {e}") from e

    def list(self, prefix: str = "") -> Iterator[str]:
        prefix = prefix.lstrip("/")
        # Walk the root, yield keys (relative to root) in sorted order.
        root_resolved = self.root.resolve()
        all_keys: list[str] = []
        for dirpath, _dirnames, filenames in os.walk(self.root):
            for fname in filenames:
                full = Path(dirpath) / fname
                rel = str(full.relative_to(root_resolved)).replace(os.sep, "/")
                if rel.startswith(prefix):
                    all_keys.append(rel)
        yield from sorted(all_keys)
