"""Drop an enriched book file into BookOrbit's watched library directory.

After biblichor's pipeline has converted, enriched, compressed, and
sent the file to Kindle, we copy it into a directory that BookOrbit's
@parcel/watcher is watching. BookOrbit handles ingest (embedded
metadata extraction, dedupe by hash) from there.

Best-effort: if the copy fails (disk full, BookOrbit not configured,
target dir missing), we raise so the caller can log it as a non-
fatal event. Never blocks the pipeline from declaring success.

Phase 6o.2 changes:
  - `compute_target_path` extracted as the single source of layout
    truth (was duplicated in migrate.py).
  - Atomic write: copy to `<file>.biblichor-tmp` then `os.replace`
    so BookOrbit's watcher never sees a half-written file.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from endless_library.download import safe_filename

log = logging.getLogger(__name__)


class BookOrbitDropError(Exception):
    """The drop did not complete; pipeline should log and continue."""


@dataclass
class DropResult:
    target_path: Path
    bytes_written: int


def compute_target_path(
    *,
    library_root: Path,
    title: str,
    author: str | None,
    file_basename: str,
    organization_mode: str = "book_per_folder",
) -> Path:
    """Compute the canonical target path for a book file. Used by both
    `drop_into_library` and `bookorbit.migrate` so the layout convention
    has exactly one source of truth.

    book_per_folder: <library>/<author>/<title>/<basename>
    book_per_file:   <library>/<basename>

    Sanitization: every path component runs through Unicode-safe
    safe_filename (Phase X.i).
    """
    safe_basename = safe_filename(file_basename)
    if organization_mode == "book_per_folder":
        author_dir = safe_filename(author or "Unknown")
        title_dir = safe_filename(title or "Unknown")
        return library_root / author_dir / title_dir / safe_basename
    if organization_mode == "book_per_file":
        return library_root / safe_basename
    raise BookOrbitDropError(f"unknown organization_mode: {organization_mode!r}")


def drop_into_library(
    src_path: Path,
    *,
    library_root: Path,
    title: str,
    author: str | None,
    organization_mode: str = "book_per_folder",
) -> DropResult:
    """Copy `src_path` into `library_root` using the configured layout.

    Atomic: writes via `<target>.biblichor-tmp` then `os.replace` so the
    BookOrbit watcher never sees a half-written file (Phase 6o.2 fix
    for the race window between copy and ingest).
    """
    src_path = Path(src_path)
    if not src_path.exists():
        raise BookOrbitDropError(f"source not found: {src_path}")
    library_root = Path(library_root)
    if not library_root.exists():
        raise BookOrbitDropError(f"library root not found: {library_root}")

    target = compute_target_path(
        library_root=library_root,
        title=title,
        author=author,
        file_basename=src_path.name,
        organization_mode=organization_mode,
    )

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".biblichor-tmp")
        shutil.copy2(src_path, tmp)
        os.replace(tmp, target)
    except OSError as e:
        # Clean up the tmp if it lingered
        import contextlib

        with contextlib.suppress(OSError):
            (target.with_name(target.name + ".biblichor-tmp")).unlink(missing_ok=True)
        raise BookOrbitDropError(f"copy failed: {e}") from e

    log.info(
        "bookorbit: dropped %s -> %s (%d bytes)",
        src_path.name,
        target.relative_to(library_root),
        target.stat().st_size,
    )
    return DropResult(target_path=target, bytes_written=target.stat().st_size)
