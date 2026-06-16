"""Unit tests for the curated keyword map + transliteration fallback.

These are tight, fast tests: no live HTTP, no Anna's. They cover:
- single-token substitution from the curated Bengali map
- multi-word phrase precedence over per-token substitution
- already-native input passes through unchanged
- unknown languages return None
- unknown Latin tokens fall back to indic-transliteration if installed

The curated map itself was ground-truthed against live Anna's results
(2026-06-02) — those liveness checks live in the PR description, not in
tests, because they'd flake on Anna's catalog churn.
"""

from __future__ import annotations

import pytest

from endless_library.search.transliteration import transliterate_query

# ---------------- curated map ----------------


def test_curated_single_token_bengali():
    """A token present in the curated map maps to its Bengali form."""
    assert transliterate_query("yoga", "bn") == "যোগ"
    assert transliterate_query("nazrul", "bn") == "নজরুল"
    assert transliterate_query("rabindranath", "bn") == "রবীন্দ্রনাথ"


def test_curated_phrase_takes_precedence_over_tokens(monkeypatch):
    """Multi-word phrases match before per-token substitution.
    `kriya yoga` -> `ক্রিয়া যোগ` via the phrase map, NOT the per-token
    map (which would also produce a valid but distinct result).
    """
    # Disable fallback to ensure the test exercises only the curated path
    monkeypatch.setattr(
        "endless_library.search.transliteration._fallback_transliteration",
        lambda q, lang: None,
    )
    assert transliterate_query("kriya yoga", "bn") == "ক্রিয়া যোগ"


def test_curated_mixed_known_and_unknown_tokens(monkeypatch):
    """Known tokens get substituted; unknown ones stay Latin. Anna's
    tokenizes on whitespace so the substituted parts still match."""
    monkeypatch.setattr(
        "endless_library.search.transliteration._fallback_transliteration",
        lambda q, lang: None,
    )
    # 'best' isn't in the map; 'yoga' is.
    out = transliterate_query("best yoga", "bn")
    assert out == "best যোগ"


def test_already_native_script_returns_none():
    """If the user already typed Bengali, don't rewrite."""
    assert transliterate_query("রবীন্দ্রনাথ", "bn") is None


def test_unknown_lang_returns_none_without_fallback(monkeypatch):
    """Lang we don't map and that indic-transliteration also can't
    handle returns None."""
    monkeypatch.setattr(
        "endless_library.search.transliteration._fallback_transliteration",
        lambda q, lang: None,
    )
    assert transliterate_query("hello", "xx") is None


def test_empty_query_returns_none():
    assert transliterate_query("", "bn") is None
    assert transliterate_query("   ", "bn") is None


def test_lang_all_falls_back(monkeypatch):
    """lang='all' has no curated map. Caller is expected to NOT call
    transliterate_query for lang=all, but be defensive — return fallback
    (which is None for 'all' since indic-transliteration doesn't know it)."""
    monkeypatch.setattr(
        "endless_library.search.transliteration._fallback_transliteration",
        lambda q, lang: None,
    )
    assert transliterate_query("kriya", "all") is None


# ---------------- fallback ----------------


def test_fallback_used_when_no_curated_hits(monkeypatch):
    """If a Latin token isn't in the curated map, we ask the fallback."""
    called = {}

    def _fake_fallback(q, lang):
        called["yes"] = (q, lang)
        return "FAKE-FALLBACK"

    monkeypatch.setattr(
        "endless_library.search.transliteration._fallback_transliteration",
        _fake_fallback,
    )
    out = transliterate_query("zzznotinmap", "bn")
    assert out == "FAKE-FALLBACK"
    assert called["yes"] == ("zzznotinmap", "bn")


def test_fallback_not_called_when_curated_hits(monkeypatch):
    """If the curated map matched, the fallback is skipped — we trust the
    curated form more."""
    sentinel = {"called": False}

    def _fake(q, lang):
        sentinel["called"] = True
        return "WRONG"

    monkeypatch.setattr("endless_library.search.transliteration._fallback_transliteration", _fake)
    out = transliterate_query("yoga", "bn")
    assert out == "যোগ"
    assert sentinel["called"] is False


# ---------------- shape ----------------


@pytest.mark.parametrize(
    "latin,bn",
    [
        ("kriya", "ক্রিয়া"),
        ("yoga", "যোগ"),
        ("vedanta", "বেদান্ত"),
        ("gita", "গীতা"),
        ("mahabharata", "মহাভারত"),
        ("bengali", "বাংলা"),
        ("kolkata", "কলকাতা"),
    ],
)
def test_curated_map_smoke(latin, bn):
    """Sanity check that the curated map contains the entries we
    ground-truthed against live Anna's on 2026-06-02."""
    assert transliterate_query(latin, "bn") == bn
