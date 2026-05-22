"""Phase 6u.7 — lazy rarfile/zipfile errors wrap to ArchiveSafetyError."""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pytest

from endless_library.security.archive_safety import (
    ArchiveSafetyError,
    safe_extract_rar,
    safe_extract_zip,
)


def test_truncated_zip_raises_archive_safety_error(tmp_path: Path) -> None:
    """A ZIP that opens fine but is truncated mid-record (lazy
    BadZipFile from infolist()) must surface as ArchiveSafetyError,
    not bubble as a raw zipfile.BadZipFile."""
    # Build a valid zip then chop off the central directory
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("book.epub", b"x" * 200)
    data = good.read_bytes()
    # Trim the last 100 bytes — destroys the central directory record
    truncated = tmp_path / "truncated.zip"
    truncated.write_bytes(data[:-100])

    with pytest.raises(ArchiveSafetyError):
        safe_extract_zip(truncated, dest_dir=tmp_path / "out")


def test_truncated_rar_raises_archive_safety_error(tmp_path: Path) -> None:
    """rarfile.BadRarFile fires lazily during infolist()/extract().
    We catch it as ArchiveSafetyError instead of letting it leak as
    'pipeline crash: Failed the read enough data: req=...'."""
    # We can't easily synthesise a valid-then-corrupt RAR without the
    # rar tool. Instead, feed safe_extract_rar a file with a valid
    # RAR magic but no body — rarfile parses past the magic, then
    # hits EOF reading the next record and raises BadRarFile.
    bad = tmp_path / "broken.rar"
    # RAR5 magic + version header, then nothing
    bad.write_bytes(b"Rar!\x1a\x07\x01\x00")
    with pytest.raises(ArchiveSafetyError):
        safe_extract_rar(bad, dest_dir=tmp_path / "out")


def test_legit_zip_still_extracts(tmp_path: Path) -> None:
    """Sanity: a well-formed ZIP wrapper still extracts cleanly with
    the lazy-error wrappers in place."""
    wrapper = tmp_path / "good.zip"
    # Inner EPUB needs to be detected as ebook content. _extract_and_pick
    # looks for ALLOWED_EXTENSIONS like .epub/.pdf/.mobi/.azw3.
    with zipfile.ZipFile(wrapper, "w") as z:
        z.writestr("book.epub", b"x" * 200)
    out = safe_extract_zip(wrapper, dest_dir=tmp_path / "out")
    assert out.exists()
    assert out.suffix == ".epub"
