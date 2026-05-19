"""Rclone-backed Store impl.

Shells out to the `rclone` binary instead of mounting (FUSE mounts
are flaky on Oracle ARM; the CLI is more reliable). One RcloneStore
instance corresponds to one configured rclone remote (e.g. "gdrive",
"hetzner-box", "s3-cold"). Set up with:

    rclone config            # interactive, one-time

Then biblichor's config.yaml references the remote name. We never
parse rclone.conf ourselves — the binary handles auth.

Operations are 1:1 with the Store protocol; each one shells out to
the appropriate rclone subcommand. We swallow rclone's stdout/stderr
into StorageError on failure so callers see one consistent exception
class regardless of backend.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

from endless_library.storage.base import KeyNotFound, StorageError

log = logging.getLogger(__name__)


class RcloneStore:
    name = "rclone"

    def __init__(
        self,
        remote: str,
        *,
        bucket_path: str = "",
        rclone_bin: str = "rclone",
        timeout_seconds: int = 600,
    ) -> None:
        """
        remote: the rclone remote name (left of the ':' in rclone.conf)
        bucket_path: optional path prefix on the remote, e.g. "biblichor/"
        """
        self.remote = remote.rstrip(":")
        self.bucket_path = bucket_path.strip("/")
        self.rclone_bin = rclone_bin
        self.timeout_seconds = timeout_seconds
        # Verify the binary exists at construction time — fail fast
        if shutil.which(self.rclone_bin) is None:
            raise StorageError(f"{self.rclone_bin} not on PATH")

    def _remote_path(self, remote_key: str) -> str:
        key = remote_key.lstrip("/")
        if self.bucket_path:
            return f"{self.remote}:{self.bucket_path}/{key}"
        return f"{self.remote}:{key}"

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        cmd = [self.rclone_bin, *args]
        log.debug("rclone: %s", " ".join(cmd))
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise StorageError(f"rclone timeout after {self.timeout_seconds}s") from e

    def put(self, local_path: Path, remote_key: str) -> None:
        local_path = Path(local_path)
        if not local_path.exists():
            raise StorageError(f"source not found: {local_path}")
        target = self._remote_path(remote_key)
        # `rclone copyto src dst` overwrites; `rclone copy` treats dst as a dir
        proc = self._run(["copyto", str(local_path), target])
        if proc.returncode != 0:
            raise StorageError(f"rclone copyto failed: {(proc.stderr or proc.stdout)[-400:]}")

    def get(self, remote_key: str, local_path: Path) -> None:
        # First check existence so we can raise KeyNotFound rather than
        # the generic rclone error
        if not self.exists(remote_key):
            raise KeyNotFound(remote_key)
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        proc = self._run(["copyto", self._remote_path(remote_key), str(local_path)])
        if proc.returncode != 0:
            raise StorageError(f"rclone copyto failed: {(proc.stderr or proc.stdout)[-400:]}")

    def exists(self, remote_key: str) -> bool:
        # `rclone lsf --files-only` returns lines for matching objects.
        # We probe by listing the exact key.
        proc = self._run(["lsf", "--files-only", self._remote_path(remote_key)])
        if proc.returncode != 0:
            # Non-existent path on most backends is also non-zero; treat as missing.
            return False
        return bool(proc.stdout.strip())

    def delete(self, remote_key: str) -> None:
        # Idempotent — rclone deletefile is fine when the file's absent
        proc = self._run(["deletefile", self._remote_path(remote_key)])
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).lower()
            if "object not found" in err or "directory not found" in err:
                return
            raise StorageError(f"rclone deletefile failed: {(proc.stderr or proc.stdout)[-400:]}")

    def list(self, prefix: str = "") -> Iterator[str]:
        scope = (
            self._remote_path(prefix)
            if prefix
            else (f"{self.remote}:{self.bucket_path}" if self.bucket_path else f"{self.remote}:")
        )
        proc = self._run(["lsf", "-R", "--files-only", scope])
        if proc.returncode != 0:
            return
        for line in proc.stdout.splitlines():
            line = line.strip().rstrip("/")
            if not line:
                continue
            # Re-add the prefix path if we listed via a deeper scope
            if prefix:
                yield prefix.rstrip("/") + "/" + line
            else:
                yield line
