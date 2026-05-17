from __future__ import annotations

from endless_library.sources.goodreads import GoodreadsRSS
from endless_library.sources.hardcover import HardcoverGQL
from endless_library.sources.manual import ManualEntry

_SOURCES = {
    "goodreads": GoodreadsRSS,
    "hardcover": HardcoverGQL,
    "manual": ManualEntry,
}


def build(name: str, **kwargs):
    if name not in _SOURCES:
        raise KeyError(f"unknown source: {name}")
    return _SOURCES[name](**kwargs)
