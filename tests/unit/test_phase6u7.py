"""Phase 6u.7 — lazy rarfile/zipfile errors wrap to ArchiveSafetyError."""

from __future__ import annotations

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


def test_safe_extract_rar_rejects_absolute_path_via_bsdtar(tmp_path: Path) -> None:
    """Phase 6w I14: post-extraction path guard catches path escapes.

    We mock bsdtar so the extraction call writes nothing inside dest_dir
    (simulating a member that resolves outside dest after bsdtar extracts it).
    _extract_and_pick then raises ArchiveSafetyError because no ebook member
    was successfully extracted inside dest. The key invariant is that
    safe_extract_rar never returns a path outside dest_dir.
    """
    from unittest.mock import MagicMock, patch

    from endless_library.security.archive_safety import (
        ArchiveSafetyError,
        safe_extract_rar,
    )

    dest = tmp_path / "out"
    dest.mkdir()

    # Fake RAR: valid RAR5 magic + padding so detect_archive returns "rar"
    # and the size cap passes.
    archive = tmp_path / "evil.rar"
    archive.write_bytes(b"Rar!\x1a\x07\x01\x00" + b"\x00" * 100)

    # A file that lives OUTSIDE dest — simulates a path-traversal escape.
    escaped_file = tmp_path / "escaped.epub"
    escaped_file.write_bytes(b"evil content")

    def fake_run(args, **kwargs):
        m = MagicMock()
        cmd_str = " ".join(str(a) for a in args)
        if "-tvf" in cmd_str:
            # Verbose size listing
            m.returncode = 0
            m.stdout = "-rw-r--r--  0 0      0       100 Jan  1  2024 book.epub\n"
            m.stderr = ""
        elif "-tf" in cmd_str:
            # Member listing: report a safe name
            m.returncode = 0
            m.stdout = "book.epub\n"
            m.stderr = ""
        elif "-xf" in cmd_str:
            # Simulate bsdtar writing OUTSIDE dest — nothing written inside dest
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
        else:
            m.returncode = 1
            m.stdout = ""
            m.stderr = "unexpected"
        return m

    with (
        patch("subprocess.run", side_effect=fake_run),
        patch(
            "shutil.which",
            return_value="/usr/bin/bsdtar",
        ),
    ):
        # No ebook ends up inside dest, so _extract_and_pick raises.
        # This is the expected safe outcome — the caller gets an error,
        # not a path pointing outside dest_dir.
        with pytest.raises(ArchiveSafetyError):
            safe_extract_rar(archive, dest_dir=dest)

    # Verify no epub was placed inside dest_dir
    assert not list(dest.rglob("*.epub")), "no epub should be inside dest"
