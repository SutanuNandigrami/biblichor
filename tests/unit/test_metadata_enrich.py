"""enrich_metadata: subprocess wrapper around ebook-meta."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from endless_library.convert import ConvertError, enrich_metadata

HAS_CALIBRE = shutil.which("ebook-meta") is not None


@pytest.mark.skipif(not HAS_CALIBRE, reason="calibre not installed")
def test_enrich_real_epub(tmp_path: Path):
    # Build a minimal valid epub via ebook-convert from a text file
    import subprocess

    src = tmp_path / "tiny.txt"
    src.write_text("Hello world. " * 500)
    epub = tmp_path / "tiny.epub"
    subprocess.run(
        [
            "ebook-convert",
            str(src),
            str(epub),
            "--output-profile=kindle_pw3",
            "--no-default-epub-cover",
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    enrich_metadata(
        epub,
        title="Test Title",
        author="Jane Doe",
        series="Test Series",
        tags=["fiction", "test"],
        isbn="9780000000000",
        language="en",
    )
    # Verify via ebook-meta read-back
    r = subprocess.run(["ebook-meta", str(epub)], capture_output=True, text=True, timeout=30)
    out = r.stdout
    assert "Test Title" in out
    assert "Jane Doe" in out
    assert "Test Series" in out


def test_enrich_missing_file(tmp_path: Path):
    with pytest.raises(ConvertError, match="not found"):
        enrich_metadata(tmp_path / "ghost.epub", title="X")


@pytest.mark.skipif(not HAS_CALIBRE, reason="calibre not installed")
def test_enrich_noop_without_fields(tmp_path: Path):
    """If no metadata fields are provided, function returns without invoking
    ebook-meta. Easiest way to test: pretend ebook-meta is /bin/false."""
    src = tmp_path / "x.epub"
    src.write_bytes(b"PK\x03\x04dummy")
    enrich_metadata(src, ebook_meta="/bin/false")  # would error if it ran
