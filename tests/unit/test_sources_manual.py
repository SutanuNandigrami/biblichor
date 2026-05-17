from __future__ import annotations

from endless_library.sources.manual import ManualEntry


def test_manual_returns_empty():
    src = ManualEntry()
    assert list(src.list_to_read(identifier="anything", token=None)) == []
    assert src.name == "manual"
