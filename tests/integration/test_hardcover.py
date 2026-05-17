from __future__ import annotations

import json
from pathlib import Path

import pytest

from endless_library.sources.hardcover import HardcoverGQL

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hardcover_to_read.json"


def test_parse_to_read_fixture():
    body = json.loads(FIXTURE.read_text(encoding="utf-8"))
    src = HardcoverGQL(post=lambda url, payload, headers: body)
    books = list(src.list_to_read(identifier="me", token="tok"))
    assert len(books) == 2
    sapiens = next(b for b in books if b.title.startswith("Sapiens"))
    assert sapiens.author == "Yuval Noah Harari"
    assert sapiens.isbn13 == "9780062316097"
    assert sapiens.source == "hardcover"
    educated = next(b for b in books if b.title == "Educated")
    assert educated.isbn13 is None


def test_requires_token():
    src = HardcoverGQL()
    with pytest.raises(ValueError):
        list(src.list_to_read(identifier="me", token=None))


def test_passes_bearer_header():
    seen = {}

    def cap(url, payload, headers):
        seen["headers"] = headers
        return {"me": [{"user_books": []}]}

    src = HardcoverGQL(post=cap)
    list(src.list_to_read(identifier="me", token="abc123"))
    assert seen["headers"]["Authorization"] == "Bearer abc123"
