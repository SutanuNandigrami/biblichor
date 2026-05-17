from __future__ import annotations

import pytest

from endless_library.domain.format_router import decide_format_action


@pytest.mark.parametrize(
    "ext,expected",
    [
        ("epub", "send_native"),
        ("azw3", "send_native"),
        ("mobi", "send_native"),
        ("pdf", "send_native"),
        ("txt", "send_native"),
        ("djvu", "convert"),
        ("fb2", "convert"),
        ("cbz", "convert"),
        ("cbr", "convert"),
        ("doc", "convert"),
        ("docx", "convert"),
        ("mp3", "skip"),
        ("zip", "skip"),
        ("", "skip"),
        ("EPUB", "send_native"),
    ],
)
def test_decide(ext: str, expected: str) -> None:
    assert decide_format_action(ext) == expected
