"""Tests for the Calibre→BookOrbit migration (Phase 6d)."""

from __future__ import annotations

from pathlib import Path

import pytest

from endless_library.bookorbit.migrate import (
    MigrateResult,
    _pick_canonical_file,
    _book_dirs,
    migrate_calibre_to_bookorbit,
)


def _calibre_layout(tmp_path: Path) -> Path:
    """Build a realistic Calibre library layout matching what's live on
    claude-1: <root>/<author>/<title (id)>/<files>."""
    root = tmp_path / "calibre-library"
    root.mkdir()

    # Author 1, two books
    (root / "Satyajit Ray").mkdir()
    b1 = root / "Satyajit Ray" / "Feluda Samagra (4)"
    b1.mkdir()
    (b1 / "Feluda Samagra - Satyajit Ray.epub").write_bytes(b"epub1")
    (b1 / "metadata.opf").write_text("<opf/>")
    (b1 / "cover.jpg").write_bytes(b"\xff\xd8\xff")

    b2 = root / "Satyajit Ray" / "Shonku Samagra (5)"
    b2.mkdir()
    (b2 / "Shonku Samagra - Satyajit Ray.epub").write_bytes(b"epub2-content")

    # Author 2, one book with multiple formats (epub preferred over pdf)
    (root / "Narayan Sanyal").mkdir()
    b3 = root / "Narayan Sanyal" / "Kantai Kantai 6 (48)"
    b3.mkdir()
    (b3 / "Kantai Kantai 6.pdf").write_bytes(b"pdf-bytes" * 100)
    (b3 / "Kantai Kantai 6.epub").write_bytes(b"epub3-converted")

    # Empty book dir (no book file — should be skipped)
    (root / "Mystery Author").mkdir()
    (root / "Mystery Author" / "Empty Book (99)").mkdir()

    # Calibre-internal hidden dirs (must be ignored)
    (root / ".calnotes").mkdir()
    (root / ".calnotes" / "backup").mkdir()

    return root


# ============ FEATURE-INTACT: discovery + canonical-file picking ============


def test_book_dirs_skip_calibre_internal(tmp_path):
    root = _calibre_layout(tmp_path)
    dirs = _book_dirs(root)
    names = [d.name for d in dirs]
    assert "Feluda Samagra (4)" in names
    assert "Shonku Samagra (5)" in names
    assert "Kantai Kantai 6 (48)" in names
    assert "Empty Book (99)" in names
    # No hidden / dotfiles
    assert not any(d.name.startswith(".") for d in dirs)
    assert not any("calnotes" in str(d) for d in dirs)


def test_pick_canonical_prefers_epub_over_pdf(tmp_path):
    """Calibre keeps every format ever attached to a book. Migration
    must pick the smaller/better one (EPUB > PDF) for BookOrbit."""
    root = _calibre_layout(tmp_path)
    multi_format = root / "Narayan Sanyal" / "Kantai Kantai 6 (48)"
    picked = _pick_canonical_file(multi_format)
    assert picked.suffix == ".epub"


def test_pick_canonical_returns_none_for_empty(tmp_path):
    root = _calibre_layout(tmp_path)
    empty = root / "Mystery Author" / "Empty Book (99)"
    assert _pick_canonical_file(empty) is None


def test_pick_canonical_ignores_opf_and_cover(tmp_path):
    """metadata.opf and cover.jpg must never be picked as the book file."""
    root = _calibre_layout(tmp_path)
    book = root / "Satyajit Ray" / "Feluda Samagra (4)"
    picked = _pick_canonical_file(book)
    assert picked.suffix == ".epub"


# ============ FEATURE-INTACT: full migration end-to-end ============


def test_migrate_copies_all_books(tmp_path):
    src = _calibre_layout(tmp_path)
    dst = tmp_path / "bookorbit-library"
    dst.mkdir()

    result = migrate_calibre_to_bookorbit(
        calibre_root=src,
        bookorbit_library_root=dst,
    )
    # 4 book dirs total, 1 empty → 3 copied
    assert result.total_books == 4
    assert result.copied == 3
    assert result.skipped_no_book_file == 1
    assert result.failed == 0
    # Verify files landed in expected places
    assert (dst / "Satyajit Ray" / "Feluda Samagra").exists()
    assert (dst / "Satyajit Ray" / "Shonku Samagra").exists()
    assert (dst / "Narayan Sanyal" / "Kantai Kantai 6").exists()


def test_migrate_drops_id_suffix_from_title(tmp_path):
    """Calibre's dir names look like 'Foo Bar (123)' — we strip the
    " (123)" id suffix when computing the BookOrbit title dir."""
    src = _calibre_layout(tmp_path)
    dst = tmp_path / "bookorbit-library"
    dst.mkdir()
    migrate_calibre_to_bookorbit(calibre_root=src, bookorbit_library_root=dst)
    # No directory should retain the ' (NNN)' suffix
    for p in dst.rglob("*"):
        if p.is_dir():
            assert not p.name.endswith(")"), f"id suffix not stripped: {p.name}"


def test_migrate_reports_lost_calibre_fields(tmp_path):
    """The migration tells the user what doesn't carry over so they
    know to set ratings/tags/etc. fresh in BookOrbit if needed."""
    src = _calibre_layout(tmp_path)
    dst = tmp_path / "bookorbit-library"
    dst.mkdir()
    result = migrate_calibre_to_bookorbit(calibre_root=src, bookorbit_library_root=dst)
    assert any("custom column" in line for line in result.lost_calibre_fields)
    assert any("rating" in line.lower() for line in result.lost_calibre_fields)


def test_migrate_progress_callback(tmp_path):
    src = _calibre_layout(tmp_path)
    dst = tmp_path / "bookorbit-library"
    dst.mkdir()
    seen: list[tuple] = []
    migrate_calibre_to_bookorbit(
        calibre_root=src,
        bookorbit_library_root=dst,
        on_progress=lambda book_dir, n, total: seen.append((book_dir.name, n, total)),
    )
    assert len(seen) == 4
    # All progress reports show the same total
    assert all(t == 4 for _, _, t in seen)


def test_migrate_is_safe_to_rerun(tmp_path):
    """Re-running the migration must NOT explode on existing target
    files. After Phase 6m.ii, the second run also skips the file
    copy entirely (pre-drop size check) instead of redundantly
    overwriting. So r2 reports the same files as skipped_existing,
    not as copied."""
    src = _calibre_layout(tmp_path)
    dst = tmp_path / "bookorbit-library"
    dst.mkdir()
    r1 = migrate_calibre_to_bookorbit(calibre_root=src, bookorbit_library_root=dst)
    r2 = migrate_calibre_to_bookorbit(calibre_root=src, bookorbit_library_root=dst)
    # First run copies; second run skips because targets already exist
    # with matching sizes.
    assert r1.copied == 3, f"first run should copy all 3, got {r1.copied}"
    assert r1.skipped_existing == 0
    assert r2.copied == 0
    assert r2.skipped_existing == 3
    assert r1.failed == 0 == r2.failed
