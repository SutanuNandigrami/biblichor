from __future__ import annotations

import pytest

from endless_library.sources.base import normalize_isbn


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9780135957059", "9780135957059"),
        ("978-0-13-595705-9", "9780135957059"),
        ("0135957052", "9780135957059"),  # ISBN-10 -> 13 (check digit recomputed)
        ("", None),
        (None, None),
        ("abc", None),
        ("12345", None),
    ],
)
def test_normalize_isbn(raw, expected):
    assert normalize_isbn(raw) == expected
