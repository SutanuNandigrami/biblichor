"""Regression + feature-intact tests for the compress module (Phase X.v).

The motivating case: id=48 (`কাঁটায়-কাঁটায় ৬`) on the live queue, a
19.8 MB scanned PDF from Anna's Archive that couldn't fit Gmail's
25 MB outbound envelope. The PDF->EPUB rescue produced a 20.4 MB
EPUB (scanned-image PDFs don't shrink via EPUB wrapping). ocrmypdf
--optimize 3 --jbig2-lossy reduced the same PDF to 13 MB in our
live benchmark with no perceptible quality loss.

The tests pin: (1) the compressor refuses to ship a larger output,
(2) missing tools fall through cleanly (don't crash the pipeline),
(3) try_compress dispatches by extension, (4) the EPUB recompress
path round-trips the zip structure.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from endless_library.compress import (
    CompressError,
    compress_epub,
    compress_pdf,
    try_compress,
)

# ============ REGRESSION: tool absence + larger-output protection ============


def test_compress_pdf_raises_when_ocrmypdf_missing(tmp_path: Path, monkeypatch):
    """If the binary isn't installed, raise CompressError — never crash
    the pipeline. The size-guard ladder catches this and falls through
    to needs_review."""
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%fake\n")
    monkeypatch.setattr("shutil.which", lambda x: None)
    with pytest.raises(CompressError, match="not installed"):
        compress_pdf(pdf)


def test_compress_pdf_raises_when_source_missing(tmp_path: Path):
    with pytest.raises(CompressError, match="source not found"):
        compress_pdf(tmp_path / "nope.pdf")


def test_compress_pdf_discards_larger_output(tmp_path: Path, monkeypatch):
    """If ocrmypdf produces a file equal-or-larger than the input,
    treat as no-op: clean up + raise. Don't ship a bloated file."""
    src = tmp_path / "in.pdf"
    src.write_bytes(b"\x00" * 1000)

    def fake_run(cmd, **kw):
        # Simulate ocrmypdf writing a LARGER output
        out = Path(cmd[-1])
        out.write_bytes(b"\x00" * 2000)

        class _Proc:
            returncode = 0
            stderr = ""
            stdout = ""

        return _Proc()

    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/ocrmypdf")
    monkeypatch.setattr("subprocess.run", fake_run)
    with pytest.raises(CompressError, match="not smaller"):
        compress_pdf(src)
    # And the larger output was cleaned up
    assert not (tmp_path / "in.opt.pdf").exists()


# ============ FEATURE-INTACT: ocrmypdf success path ============


def test_compress_pdf_returns_smaller_file(tmp_path: Path, monkeypatch):
    """Happy path: ocrmypdf writes a smaller PDF; compress_pdf returns
    the new path."""
    src = tmp_path / "in.pdf"
    src.write_bytes(b"\x00" * 10_000)

    captured_cmd: list[list[str]] = []

    def fake_run(cmd, **kw):
        captured_cmd.append(list(cmd))
        out = Path(cmd[-1])
        out.write_bytes(b"\x00" * 4_000)  # smaller

        class _Proc:
            returncode = 0
            stderr = ""
            stdout = ""

        return _Proc()

    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/ocrmypdf")
    monkeypatch.setattr("subprocess.run", fake_run)

    result = compress_pdf(src, aggressive=False)
    assert result == tmp_path / "in.opt.pdf"
    assert result.stat().st_size < src.stat().st_size

    # And the right flags were passed
    cmd = captured_cmd[0]
    assert "--optimize" in cmd and "3" in cmd
    assert "--skip-text" in cmd
    assert "--jbig2-lossy" in cmd
    # Conservative mode does NOT pass jpeg-quality
    assert "--jpeg-quality" not in cmd


def test_compress_pdf_aggressive_adds_jpeg_quality(tmp_path: Path, monkeypatch):
    """The aggressive variant passes --jpeg-quality 50 + --png-quality 60."""
    src = tmp_path / "in.pdf"
    src.write_bytes(b"\x00" * 10_000)
    captured_cmd: list[list[str]] = []

    def fake_run(cmd, **kw):
        captured_cmd.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"\x00" * 1000)

        class _Proc:
            returncode = 0
            stderr = ""
            stdout = ""

        return _Proc()

    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/ocrmypdf")
    monkeypatch.setattr("subprocess.run", fake_run)

    compress_pdf(src, aggressive=True)
    cmd = captured_cmd[0]
    assert "--jpeg-quality" in cmd
    assert "50" in cmd
    assert "--png-quality" in cmd


# ============ EPUB compression ============


def _build_test_epub(path: Path, *, image_bytes: bytes) -> None:
    """Create a minimal EPUB with one image entry, valid zip structure."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", "<container/>")
        z.writestr("OEBPS/content.opf", "<package/>")
        z.writestr("OEBPS/cover.png", image_bytes)
        z.writestr("OEBPS/chapter.xhtml", "<html/>")


def test_compress_epub_preserves_zip_structure(tmp_path: Path, monkeypatch):
    """If pngquant manages to shrink the cover, the EPUB shrinks; the
    zip structure (entry names + mimetype) round-trips intact."""
    big_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50_000  # not real PNG but bytes
    src = tmp_path / "in.epub"
    _build_test_epub(src, image_bytes=big_png)

    def fake_run(cmd, **kw):
        # pngquant emits a smaller output via stdout
        if "pngquant" in cmd[0] if isinstance(cmd[0], str) else False:
            pass

        class _Proc:
            returncode = 0
            stderr = ""
            stdout = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10_000  # smaller

        return _Proc()

    monkeypatch.setattr("shutil.which", lambda x: f"/usr/bin/{x}")
    monkeypatch.setattr("subprocess.run", fake_run)

    result = compress_epub(src)
    assert result.exists()
    assert result.stat().st_size < src.stat().st_size

    # Same entries, mimetype first
    with zipfile.ZipFile(result) as z:
        names = z.namelist()
        assert names[0] == "mimetype"
        assert "META-INF/container.xml" in names
        assert "OEBPS/content.opf" in names
        assert "OEBPS/cover.png" in names
        assert "OEBPS/chapter.xhtml" in names


def test_compress_epub_discards_if_no_size_win(tmp_path: Path, monkeypatch):
    """If image tools aren't installed or yield no win, the repack might
    end up the same size. Don't ship that — caller wants a smaller file
    or nothing."""
    src = tmp_path / "in.epub"
    _build_test_epub(src, image_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 1000)

    # Both image tools missing
    monkeypatch.setattr("shutil.which", lambda x: None)
    with pytest.raises(CompressError, match="not smaller"):
        compress_epub(src)


# ============ try_compress dispatcher ============


def test_try_compress_returns_none_for_unknown_extension(tmp_path: Path):
    """txt, html, etc. are not handled — return None so the caller
    falls through to needs_review."""
    src = tmp_path / "x.txt"
    src.write_text("not a book format we know")
    assert try_compress(src, target_bytes=1) is None


def test_try_compress_returns_none_when_pdf_compress_fails(tmp_path: Path, monkeypatch):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF-1.4\n" + b"\x00" * 10_000)
    monkeypatch.setattr("shutil.which", lambda x: None)  # ocrmypdf absent
    assert try_compress(src, target_bytes=100) is None


def test_try_compress_pdf_returns_path_when_fits(tmp_path: Path, monkeypatch):
    """Conservative pass produces output under target_bytes -> return it."""
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF-1.4\n" + b"\x00" * 10_000)

    call_count = [0]

    def fake_run(cmd, **kw):
        call_count[0] += 1
        Path(cmd[-1]).write_bytes(b"\x00" * 500)

        class _Proc:
            returncode = 0
            stderr = ""
            stdout = ""

        return _Proc()

    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/ocrmypdf")
    monkeypatch.setattr("subprocess.run", fake_run)

    result = try_compress(src, target_bytes=1000)
    assert result is not None
    assert result.stat().st_size <= 1000
    # Conservative pass was enough; aggressive shouldn't have fired
    assert call_count[0] == 1


def test_try_compress_pdf_falls_through_to_aggressive(tmp_path: Path, monkeypatch):
    """Conservative produces smaller-but-still-over-cap output -> try
    aggressive. Pin the ladder ordering."""
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF-1.4\n" + b"\x00" * 10_000)

    call_count = [0]

    def fake_run(cmd, **kw):
        call_count[0] += 1
        # First pass: smaller but still over target. Second: fits.
        if call_count[0] == 1:
            Path(cmd[-1]).write_bytes(b"\x00" * 5_000)
        else:
            Path(cmd[-1]).write_bytes(b"\x00" * 500)

        class _Proc:
            returncode = 0
            stderr = ""
            stdout = ""

        return _Proc()

    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/ocrmypdf")
    monkeypatch.setattr("subprocess.run", fake_run)

    result = try_compress(src, target_bytes=1000)
    assert result is not None
    assert call_count[0] == 2  # both passes ran
    # And the result is the aggressive output
    assert result.stat().st_size <= 1000


# ============ FUNCTIONAL: end-to-end with real ocrmypdf on a real PDF ============


def test_compress_pdf_round_trips_real_pdf(tmp_path: Path):
    import os as _os

    import pytest as _pt

    if not _os.path.exists("/home/ubuntu/endless-library/data/books"):
        _pt.skip("VPS-only data files not available in CI", allow_module_level=False)

    """If ocrmypdf is on the box and we have a real PDF to test against,
    confirm a real round-trip. Skipped in CI where neither is true."""
    if shutil.which("ocrmypdf") is None:
        pytest.skip("ocrmypdf not installed on this machine")
    candidates = [
        "/home/ubuntu/endless-library/data/books/কাঁটায়-কাঁটায় ৬ -- Narayan Sanyal.pdf",
        "/home/ubuntu/endless-library/data/books/Kaalbela _ -- Samaresh Majumdar _ _.pdf",
    ]
    src = None
    for c in candidates:
        if Path(c).exists():
            src = Path(c)
            break
    if src is None:
        pytest.skip("no real PDF available for round-trip")

    work = tmp_path / "in.pdf"
    shutil.copy(src, work)
    out = compress_pdf(work, aggressive=False, timeout_seconds=600)
    assert out.exists()
    assert out.stat().st_size < work.stat().st_size
    # And the output is still a valid PDF (starts with %PDF)
    assert out.read_bytes()[:4] == b"%PDF"
