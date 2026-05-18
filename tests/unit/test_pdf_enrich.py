"""Regression + feature-intact tests for PDF metadata enrichment.

Audit context: 15 sent PDFs on the live queue have transliterated /
filename-derived metadata that Kindle then displays as broken book
names. The fix extends enrich_metadata's gating in process_one to
include .pdf so the DB's known-good Bengali title/author get written
to the PDF before SMTP send.

Regression: confirm the format gate now includes .pdf.
Feature-intact: EPUB/AZW3/MOBI still pass; unsupported formats
still skip enrichment.
"""

from __future__ import annotations

import inspect

from endless_library import pipeline


def _get_enrich_gating_line() -> str:
    """Pull the exact line that gates enrich_metadata so we can assert
    against the set of allowed extensions. Pinning by source inspection
    is fragile but the alternative is mocking the whole pipeline."""
    src = inspect.getsource(pipeline)
    # Find the line with the format-extension set used before enrich_metadata
    for line in src.splitlines():
        if (
            'file_path.suffix' in line
            and 'in {' in line
            and '.epub' in line
            and '.mobi' in line
        ):
            return line
    return ""


# ============ REGRESSION ============


def test_pdf_is_in_enrich_set():
    """Pin the fix: PDFs go through enrich_metadata now."""
    line = _get_enrich_gating_line()
    assert line, "could not locate the enrich-gating line in pipeline.py"
    assert '".pdf"' in line, f"PDF missing from enrich set: {line!r}"


# ============ FEATURE-INTACT ============


def test_epub_still_in_enrich_set():
    line = _get_enrich_gating_line()
    assert '".epub"' in line


def test_azw3_still_in_enrich_set():
    line = _get_enrich_gating_line()
    assert '".azw3"' in line


def test_mobi_still_in_enrich_set():
    line = _get_enrich_gating_line()
    assert '".mobi"' in line


def test_unsupported_formats_not_in_set():
    """Formats Calibre's ebook-meta can't write to (txt, html, etc.)
    must NOT be in the enrich set."""
    line = _get_enrich_gating_line()
    for fmt in ('".txt"', '".html"', '".cbz"', '".kfx"'):
        assert fmt not in line, f"{fmt} should not trigger enrich: {line!r}"


# ============ FUNCTIONAL TEST: enrich_metadata writes the PDF info dict ============


def test_enrich_metadata_writes_pdf_title(tmp_path):
    """End-to-end via a real PDF: enrich_metadata --title writes the
    PDF info dict, and ebook-meta can read it back. This is what makes
    Kindle show the right title when ingesting the personal doc."""
    import shutil
    import subprocess
    from pathlib import Path

    from endless_library.convert import enrich_metadata

    # Create a minimal valid PDF via reportlab if available, else copy a
    # known PDF off disk. We need ANY PDF for ebook-meta to round-trip.
    src_candidates = [
        "/home/ubuntu/endless-library/data/books/Ghonada Somogra 1 _ _ _ -- Premendra Mitra _ _.pdf",
        "/home/ubuntu/endless-library/data/books/Kaalbela _ -- Samaresh Majumdar _ _.pdf",
    ]
    src = None
    for c in src_candidates:
        if Path(c).exists():
            src = Path(c)
            break
    if src is None:
        # No real PDF available in CI — skip the functional half.
        import pytest
        pytest.skip("no real PDF available for functional enrich test")

    dst = tmp_path / "test.pdf"
    shutil.copy(src, dst)

    # Enrich with a Bengali title + Latin author
    enrich_metadata(dst, title="ঘনাদা সমগ্র ১", author="Premendra Mitra")

    # Read it back via ebook-meta
    out = subprocess.run(
        ["ebook-meta", str(dst)], capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0
    # The Bengali title must round-trip
    assert "ঘনাদা সমগ্র ১" in out.stdout, f"Bengali title lost: {out.stdout[:300]}"
    # And the author
    assert "Premendra Mitra" in out.stdout
