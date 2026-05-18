"""Build a Store instance from config.

Used by both the live pipeline (one-shot at app startup) and the
backup/restore commands.
"""

from __future__ import annotations

from pathlib import Path

from endless_library.storage.base import Store
from endless_library.storage.hybrid import HybridMode, HybridStore
from endless_library.storage.local import LocalStore
from endless_library.storage.rclone import RcloneStore


def build_store(cfg, *, data_root: Path) -> Store:
    """Construct the configured Store. `cfg` is the StorageCfg
    section from config.yaml; `data_root` is the existing books_dir
    so LocalStore lands files in the expected place by default."""
    backend = (cfg.backend or "local").lower()
    if backend == "local":
        return _build_local(cfg, data_root)
    if backend == "rclone":
        return _build_rclone(cfg)
    if backend == "hybrid":
        primary = _build_local(cfg, data_root)
        backup = _build_rclone(cfg)
        return HybridStore(primary, backup, mode=_validate_mode(cfg.hybrid_mode))
    raise ValueError(f"unknown storage backend: {backend!r}")


def _build_local(cfg, data_root: Path) -> LocalStore:
    root = Path(cfg.local_root) if cfg.local_root else data_root
    return LocalStore(root)


def _build_rclone(cfg) -> RcloneStore:
    if not cfg.rclone_remote:
        raise ValueError("storage.rclone_remote is required for rclone/hybrid backends")
    return RcloneStore(
        remote=cfg.rclone_remote,
        bucket_path=cfg.rclone_bucket_path or "",
    )


def _validate_mode(mode: str | None) -> HybridMode:
    m = (mode or "mirror").lower()
    if m not in ("mirror", "scheduled"):
        raise ValueError(f"storage.hybrid_mode must be 'mirror' or 'scheduled', got {m!r}")
    return m  # type: ignore[return-value]
