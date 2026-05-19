"""Drop an enriched book file into BookOrbit's watched library directory.

After biblichor's pipeline has converted, enriched, compressed, and
sent the file to Kindle, we copy it into a directory that BookOrbit's
@parcel/watcher is watching. BookOrbit handles ingest (embedded
metadata extraction, dedupe by hash) from there.

Best-effort: if the copy fails (disk full, BookOrbit not configured,
target dir missing), we raise so the caller can log it as a non-
fatal event. Never blocks the pipeline from declaring success.
"""

from __future__ import annotations

import logging
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


def drop_into_library(
    src_path: Path,
    *,
    library_root: Path,
    title: str,
    author: str | None,
    organization_mode: str = "book_per_folder",
) -> DropResult:
    """Copy `src_path` into `library_root` using the configured layout.

    book_per_folder: <library>/<author>/<title>/<basename>
    book_per_file:   <library>/<basename>

    Filenames are sanitized via the Unicode-safe safe_filename from
    Phase X.i so Bengali/CJK titles survive intact.
    """
    src_path = Path(src_path)
    if not src_path.exists():
        raise BookOrbitDropError(f"source not found: {src_path}")
    library_root = Path(library_root)
    if not library_root.exists():
        raise BookOrbitDropError(f"library root not found: {library_root}")

    file_basename = safe_filename(src_path.name)
    if organization_mode == "book_per_folder":
        author_dir = safe_filename(author or "Unknown")
        title_dir = safe_filename(title or "Unknown")
        target_dir = library_root / author_dir / title_dir
    elif organization_mode == "book_per_file":
        target_dir = library_root
    else:
        raise BookOrbitDropError(f"unknown organization_mode: {organization_mode!r}")

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / file_basename
        shutil.copy2(src_path, target)
    except OSError as e:
        raise BookOrbitDropError(f"copy failed: {e}") from e

    log.info(
        "bookorbit: dropped %s -> %s (%d bytes)",
        src_path.name, target.relative_to(library_root), target.stat().st_size,
    )
    return DropResult(target_path=target, bytes_written=target.stat().st_size)
