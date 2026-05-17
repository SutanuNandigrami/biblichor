from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from endless_library.convert import ConvertError, convert_to_epub

HAS_CALIBRE = shutil.which("ebook-convert") is not None


@pytest.mark.skipif(not HAS_CALIBRE, reason="calibre not installed")
def test_convert_txt_to_epub(tmp_path: Path):
    src = tmp_path / "tiny.txt"
    body = "Hello, world!\n\nThis is a test book.\n\n" * 200
    src.write_text(body, encoding="utf-8")
    result = convert_to_epub(src, timeout_seconds=120, min_output_bytes=1_000)
    assert result.path.suffix == ".epub"
    assert result.path.exists()
    assert result.path.stat().st_size >= 1_000


def test_missing_source(tmp_path: Path):
    with pytest.raises(ConvertError, match="not found"):
        convert_to_epub(tmp_path / "ghost.djvu")


def test_uses_stub_binary_failure(tmp_path: Path):
    """If we point convert at /bin/false it must surface a non-zero exit."""
    src = tmp_path / "x.txt"
    src.write_text("hello")
    with pytest.raises(ConvertError, match="exit"):
        convert_to_epub(src, ebook_convert="/bin/false")
