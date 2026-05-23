"""Tests for security/archive_safety + security/unpack.

ClamAV is tested via shutil.which monkeypatch + a fake subprocess.run; we
never actually invoke clamscan in CI.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from endless_library.security.archive_safety import (
    ALLOWED_EXTENSIONS,
    ArchiveSafetyError,
    SafetyLimits,
    detect_archive,
    is_epub_zip,
    safe_extract_zip,
)
from endless_library.security.clamav import ScanResult, scan
from endless_library.security.unpack import UnpackError, unpack_if_archive

# ============ utilities ============


def _zip_with_members(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)


def _minimal_epub(path: Path) -> None:
    """Real, valid (if tiny) EPUB at `path`."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            zipfile.ZipInfo("mimetype", date_time=(2020, 1, 1, 0, 0, 0)),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0"/>',
        )
    path.write_bytes(buf.getvalue())


# ============ detect_archive ============


def test_detect_archive_zip(tmp_path: Path):
    p = tmp_path / "x.zip"
    _zip_with_members(p, {"book.epub": b"x"})
    assert detect_archive(p) == "zip"


def test_detect_archive_rar(tmp_path: Path):
    p = tmp_path / "x.rar"
    p.write_bytes(b"Rar!\x1a\x07\x00stuff")
    assert detect_archive(p) == "rar"


def test_detect_archive_rar5(tmp_path: Path):
    p = tmp_path / "x.rar"
    p.write_bytes(b"Rar!\x1a\x07\x01\x00stuff")  # RAR 5
    assert detect_archive(p) == "rar"


def test_detect_archive_plain_text(tmp_path: Path):
    p = tmp_path / "x.txt"
    p.write_bytes(b"hello world")
    assert detect_archive(p) is None


def test_detect_archive_missing(tmp_path: Path):
    assert detect_archive(tmp_path / "nope") is None


# ============ is_epub_zip ============


def test_is_epub_zip_real(tmp_path: Path):
    p = tmp_path / "book.epub"
    _minimal_epub(p)
    assert is_epub_zip(p)


def test_is_epub_zip_not_an_epub(tmp_path: Path):
    p = tmp_path / "wrapped.zip"
    _zip_with_members(p, {"book.epub": b"contents"})
    assert not is_epub_zip(p)


def test_is_epub_zip_malformed(tmp_path: Path):
    p = tmp_path / "x.zip"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 32)  # PK magic but corrupt
    assert not is_epub_zip(p)


# ============ safe_extract_zip ============


def test_safe_extract_zip_picks_epub_among_siblings(tmp_path: Path):
    p = tmp_path / "x.zip"
    _zip_with_members(
        p,
        {
            "cover.jpg": b"fakeimg",
            "book.epub": b"epubbytes",
            "metadata.opf": b"<opf/>",
        },
    )
    out = safe_extract_zip(p, dest_dir=tmp_path / "unpack")
    assert out.name == "book.epub"
    assert out.read_bytes() == b"epubbytes"


def test_safe_extract_zip_rejects_path_traversal(tmp_path: Path):
    p = tmp_path / "evil.zip"
    _zip_with_members(p, {"../escape.epub": b"x"})
    with pytest.raises(ArchiveSafetyError, match="path traversal"):
        safe_extract_zip(p, dest_dir=tmp_path / "out")


def test_safe_extract_zip_rejects_absolute_path(tmp_path: Path):
    p = tmp_path / "evil.zip"
    _zip_with_members(p, {"/etc/passwd": b"x"})  # zipfile accepts this
    with pytest.raises(ArchiveSafetyError, match="absolute-path"):
        safe_extract_zip(p, dest_dir=tmp_path / "out")


def test_safe_extract_zip_skips_disallowed_extension_but_extracts_ebook(tmp_path: Path):
    """Phase 6u.7c: disallowed extensions are filtered out (skipped),
    NOT a hard archive-level rejection. As long as ONE ebook member is
    extractable, the archive succeeds. This matches the kindlebangla
    case where RARs include .epub + .kfx + .jpg + .opf siblings — we
    only need the .epub.
    """
    p = tmp_path / "x.zip"
    _zip_with_members(p, {"payload.exe": b"MZ", "book.epub": b"x"})
    out = safe_extract_zip(p, dest_dir=tmp_path / "out")
    assert out.name == "book.epub"
    # The .exe member must NOT have been extracted.
    assert not (tmp_path / "out" / "payload.exe").exists()


def test_safe_extract_zip_fails_when_no_ebook_member(tmp_path: Path):
    """If every member is a non-ebook format, there's nothing for the
    pipeline to do — fail cleanly. .txt + .jpg are allowed formats
    (kindlebangla packs covers + readmes) but neither is an ebook."""
    p = tmp_path / "x.zip"
    _zip_with_members(p, {"cover.jpg": b"\xff\xd8\xff", "readme.txt": b"hello"})
    with pytest.raises(ArchiveSafetyError, match="no ebook file"):
        safe_extract_zip(p, dest_dir=tmp_path / "out")


def test_safe_extract_zip_rejects_nested_archive(tmp_path: Path):
    p = tmp_path / "x.zip"
    _zip_with_members(p, {"book.epub": b"x", "wrapped.zip": b"PK\x03\x04"})
    with pytest.raises(ArchiveSafetyError, match="nested archive"):
        safe_extract_zip(p, dest_dir=tmp_path / "out")


def test_safe_extract_zip_rejects_zip_bomb(tmp_path: Path):
    """Simulated by writing many large members."""
    p = tmp_path / "x.zip"
    big = b"a" * (5 * 1024 * 1024)  # 5MB each member
    _zip_with_members(p, {f"file{i}.epub": big for i in range(20)})  # 100MB total
    limits = SafetyLimits(max_extracted_size_mb=50)  # 50MB cap
    with pytest.raises(ArchiveSafetyError, match="uncompressed size"):
        safe_extract_zip(p, dest_dir=tmp_path / "out", limits=limits)


def test_safe_extract_zip_rejects_too_many_members(tmp_path: Path):
    p = tmp_path / "x.zip"
    _zip_with_members(p, {f"f{i}.epub": b"x" for i in range(60)})
    limits = SafetyLimits(max_members=50)
    with pytest.raises(ArchiveSafetyError, match="too many members"):
        safe_extract_zip(p, dest_dir=tmp_path / "out", limits=limits)


def test_safe_extract_zip_rejects_no_ebook(tmp_path: Path):
    p = tmp_path / "x.zip"
    _zip_with_members(p, {"cover.jpg": b"img", "metadata.opf": b"<opf/>"})
    with pytest.raises(ArchiveSafetyError, match="no ebook"):
        safe_extract_zip(p, dest_dir=tmp_path / "out")


def test_safe_extract_zip_rejects_invalid_zip(tmp_path: Path):
    """A file with PK magic but no central directory — defensive guard for the
    integration test fixture scenario."""
    p = tmp_path / "x.zip"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
    with pytest.raises(ArchiveSafetyError, match="invalid ZIP"):
        safe_extract_zip(p, dest_dir=tmp_path / "out")


# ============ unpack_if_archive end-to-end ============


def test_unpack_passthrough_for_bare_epub(tmp_path: Path):
    p = tmp_path / "book.epub"
    _minimal_epub(p)
    result = unpack_if_archive(p)
    assert not result.was_archive
    assert result.path == p


def test_unpack_extracts_zip_wrapped_epub(tmp_path: Path):
    inner = tmp_path / "inner.epub"
    _minimal_epub(inner)
    wrapper = tmp_path / "book.zip"
    _zip_with_members(wrapper, {"book.epub": inner.read_bytes()})
    inner.unlink()  # leave only the wrapper
    result = unpack_if_archive(wrapper)
    assert result.was_archive
    # Phase 6u.5c: outer downloaded filename is the slot (collision-proof).
    assert result.path.name == "book.zip"
    assert result.path.exists()
    # The archive itself was renamed .orig
    assert (tmp_path / "book.zip.orig").exists()


def test_unpack_fails_on_safety_violation(tmp_path: Path):
    """A wrapper that contains a forbidden extension must fail loudly."""
    p = tmp_path / "evil.zip"
    _zip_with_members(p, {"payload.exe": b"MZ"})
    with pytest.raises(UnpackError, match="hygiene violation"):
        unpack_if_archive(p)


def test_unpack_raises_on_orig_rename_failure(tmp_path: Path, monkeypatch):
    """m-NEW-4: if the .orig rename fails, UnpackError is raised and the
    original file is not overwritten (no silent data loss)."""
    inner = tmp_path / "inner.epub"
    _minimal_epub(inner)
    wrapper = tmp_path / "book.zip"
    _zip_with_members(wrapper, {"book.epub": inner.read_bytes()})
    inner.unlink()

    original_bytes = wrapper.read_bytes()

    # Monkeypatch Path.rename to raise OSError only when the destination
    # has the .orig suffix (i.e. the preservation rename).
    real_rename = wrapper.__class__.rename

    def _failing_rename(self, target):
        if str(target).endswith(".orig"):
            raise OSError("simulated rename failure")
        return real_rename(self, target)

    monkeypatch.setattr(wrapper.__class__, "rename", _failing_rename)

    with pytest.raises(UnpackError, match="refused to overwrite"):
        unpack_if_archive(wrapper)

    # Original file must still exist and be intact
    assert wrapper.exists(), "Original archive was lost after failed .orig rename"
    assert wrapper.read_bytes() == original_bytes, "Original archive content was corrupted"


# ============ clamav.scan ============


def test_clamav_scan_skips_when_not_installed_and_not_required(tmp_path, monkeypatch):
    monkeypatch.setattr("endless_library.security.clamav.is_installed", lambda: False)
    p = tmp_path / "book.epub"
    _minimal_epub(p)
    r = scan(p, require=False)
    assert isinstance(r, ScanResult)
    assert r.ok and r.skipped


def test_clamav_scan_fails_when_required_and_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr("endless_library.security.clamav.is_installed", lambda: False)
    p = tmp_path / "book.epub"
    _minimal_epub(p)
    r = scan(p, require=True)
    assert not r.ok
    assert "not installed" in (r.detail or "")


def test_clamav_scan_parses_infected_output(tmp_path, monkeypatch):
    """Mock subprocess.run returning exit 1 + clamscan FOUND output."""
    monkeypatch.setattr("endless_library.security.clamav.is_installed", lambda: True)

    class FakeProc:
        returncode = 1
        stdout = "/path/to/book.epub: Win.Trojan.Foo FOUND\n"
        stderr = ""

    monkeypatch.setattr(
        "endless_library.security.clamav.subprocess.run",
        lambda *_args, **_kw: FakeProc(),
    )

    p = tmp_path / "book.epub"
    _minimal_epub(p)
    r = scan(p)
    assert not r.ok
    assert r.threat == "Win.Trojan.Foo"


def test_clamav_scan_clean_passes(tmp_path, monkeypatch):
    monkeypatch.setattr("endless_library.security.clamav.is_installed", lambda: True)

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        "endless_library.security.clamav.subprocess.run",
        lambda *_args, **_kw: FakeProc(),
    )
    p = tmp_path / "book.epub"
    _minimal_epub(p)
    r = scan(p)
    assert r.ok and not r.skipped


# ============ extension whitelist sanity ============


def test_extension_whitelist_includes_expected():
    """Just a guard so a future refactor doesn't accidentally strip ebook
    extensions from the whitelist."""
    for ext in (".epub", ".pdf", ".azw3", ".mobi"):
        assert ext in ALLOWED_EXTENSIONS
    for forbidden in (".exe", ".dll", ".bat", ".sh", ".so", ".lnk"):
        assert forbidden not in ALLOWED_EXTENSIONS
