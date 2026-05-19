"""Migrate an existing Calibre library into BookOrbit's watched directory.

Walks `data/calibre-library/<author>/<title>/` recursively, finds the
canonical book file in each leaf dir (preferring epub > azw3 > mobi >
pdf > others), copies it into BookOrbit's library using
drop_into_library (Phase 6c), and optionally triggers a manual
BookOrbit scan when done.

What we deliberately do NOT migrate:
- Calibre's metadata.db sidecar (BookOrbit has no Calibre adapter).
- Custom columns, ratings, comments set only in Calibre-Web.
- Per-book OPF/cover files (BookOrbit reads embedded metadata and
  generates covers — keeping the Calibre OPFs would just be noise).

The actual book metadata is preserved because biblichor's Phase X.ii
already writes title/author/series/ISBN into the file itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from endless_library.bookorbit.drop import (
    BookOrbitDropError,
    drop_into_library,
)

log = logging.getLogger(__name__)

# Preference order for picking a single canonical file per book dir
PREFERRED_FORMATS = (".epub", ".kepub", ".azw3", ".mobi", ".pdf", ".cbz", ".cbr", ".fb2", ".m4b")


@dataclass
class MigrateResult:
    total_books: int = 0
    copied: int = 0
    skipped_existing: int = 0
    skipped_no_book_file: int = 0
    failed: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    lost_calibre_fields: list[str] = field(default_factory=list)


def _pick_canonical_file(book_dir: Path) -> Path | None:
    """Calibre stores one ebook file per book dir (plus metadata.opf
    and cover.jpg). We prefer .epub > .azw3 > .mobi > .pdf > others.
    """
    candidates: list[Path] = []
    for ext in PREFERRED_FORMATS:
        candidates += sorted(book_dir.glob(f"*{ext}"))
        if candidates:
            return candidates[0]
    # No known book format — try any non-metadata file
    for child in sorted(book_dir.iterdir()):
        if child.is_file() and child.suffix.lower() not in (".opf", ".jpg", ".png"):
            return child
    return None


def _book_dirs(calibre_root: Path) -> list[Path]:
    """Yield <author>/<title> directories. Calibre lays out as
    <root>/<author>/<title (id)>/files."""
    out: list[Path] = []
    for author_dir in sorted(calibre_root.iterdir()):
        if not author_dir.is_dir() or author_dir.name.startswith("."):
            continue
        for book_dir in sorted(author_dir.iterdir()):
            if book_dir.is_dir():
                out.append(book_dir)
    return out


def migrate_calibre_to_bookorbit(
    *,
    calibre_root: Path,
    bookorbit_library_root: Path,
    organization_mode: str = "book_per_folder",
    on_progress=None,
) -> MigrateResult:
    """Walk a Calibre library + copy each book into the BookOrbit
    library via Phase 6c's drop_into_library.

    Idempotent: BookOrbit's scanner dedupes by SHA, but to avoid
    expensive double-writes we also short-circuit when the target
    file already exists with matching size.

    `on_progress(book_dir, n, total)` fires after each successful
    or failed migration so the CLI can print a progress bar.
    """
    result = MigrateResult()
    book_dirs = _book_dirs(calibre_root)
    result.total_books = len(book_dirs)
    log.info("migrate: %d book dirs in %s", result.total_books, calibre_root)

    for i, book_dir in enumerate(book_dirs, start=1):
        canonical = _pick_canonical_file(book_dir)
        if canonical is None:
            result.skipped_no_book_file += 1
            if on_progress:
                on_progress(book_dir, i, result.total_books)
            continue

        author = book_dir.parent.name  # Calibre's <author> dir
        # Trim trailing " (123)" id suffix from Calibre's <title> dir
        raw_title = book_dir.name
        title = raw_title.rsplit(" (", 1)[0] if raw_title.endswith(")") else raw_title

        # Phase 6m.ii: real pre-drop skip. Compute the destination path
        # the same way drop_into_library does so we can short-circuit
        # before any I/O when the file already exists with matching
        # size. BookOrbit's hash dedup would also catch duplicates on
        # ingest, but this avoids the copy IO entirely.
        from endless_library.download import safe_filename

        if organization_mode == "book_per_folder":
            target_dir = bookorbit_library_root / safe_filename(author) / safe_filename(title)
        else:
            target_dir = bookorbit_library_root
        target_path = target_dir / safe_filename(canonical.name)
        if target_path.exists() and target_path.stat().st_size == canonical.stat().st_size:
            result.skipped_existing += 1
            if on_progress:
                on_progress(book_dir, i, result.total_books)
            continue

        try:
            drop_into_library(
                canonical,
                library_root=bookorbit_library_root,
                title=title,
                author=author,
                organization_mode=organization_mode,
            )
            result.copied += 1
        except BookOrbitDropError as e:
            result.failed += 1
            result.errors.append((str(canonical), str(e)))
            log.warning("migrate fail %s: %s", canonical.name, e)

        if on_progress:
            on_progress(book_dir, i, result.total_books)

    # Always document what was NOT migrated
    result.lost_calibre_fields = [
        "ratings (Calibre-Web star ratings)",
        "custom columns (#shelf, #read_date, etc)",
        "user comments / annotations",
        "tags set only in Calibre-Web (book-embedded tags DO migrate)",
    ]
    return result
