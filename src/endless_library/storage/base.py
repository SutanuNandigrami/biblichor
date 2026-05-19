"""Storage abstraction for biblichor.

Defines the `Store` Protocol that every backend implements (local
filesystem, rclone-driven remotes, hybrid combinations). The pipeline
talks to a `Store` instance instead of poking at `Path` directly so we
can swap in Drive / Hetzner / S3 / B2 without changing pipeline code.

Key design notes:
- All paths are relative `remote_key` strings (no leading slashes).
- Backends are not required to be local. `get()` always materializes
  a local file at the requested path; `put()` accepts a local path
  and writes it to the backend.
- Errors raise `StorageError` (or a subclass). Callers should treat
  any storage failure as a non-fatal warning unless they explicitly
  need the I/O to have succeeded.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, runtime_checkable


class StorageError(Exception):
    """Any backend-level failure (network, permissions, missing key)."""


class KeyNotFound(StorageError):
    """Raised by `get`, `delete`, or any read op when the key doesn't
    exist on the backend. Caller can either treat as benign (idempotent
    delete) or surface to the user (missing backup)."""


@runtime_checkable
class Store(Protocol):
    """Backend-agnostic blob storage.

    Implementations: LocalStore (default), RcloneStore (any rclone
    remote), HybridStore (primary + backup mirror).
    """

    name: str  # human-readable identifier for logs/UI

    def put(self, local_path: Path, remote_key: str) -> None:
        """Upload `local_path` to the backend at `remote_key`.
        Overwrites any existing object at that key. Raises
        StorageError on failure."""
        ...

    def get(self, remote_key: str, local_path: Path) -> None:
        """Download the object at `remote_key` to `local_path`. The
        target directory must already exist. Raises KeyNotFound if
        the key doesn't exist; StorageError on other failures."""
        ...

    def exists(self, remote_key: str) -> bool:
        """Return True iff the key exists on the backend. Must NOT
        raise on a missing key (return False instead)."""
        ...

    def delete(self, remote_key: str) -> None:
        """Remove the object. Idempotent: missing key is not an
        error (no KeyNotFound)."""
        ...

    def list(self, prefix: str = "") -> Iterator[str]:
        """Yield every remote_key matching the prefix, in
        lexicographic order. Empty prefix lists everything."""
        ...
