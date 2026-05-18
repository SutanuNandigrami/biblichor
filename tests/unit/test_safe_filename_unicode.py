"""Regression + feature-intact tests for the Unicode-safe safe_filename.

Original bug: 54 books on the live box had every Bengali character
mangled into `_`. EPUB enrichment hid the impact (Kindle reads
embedded metadata), but PDFs with no/transliterated metadata fell
back to the broken filename.

Regression: Bengali / CJK / Cyrillic / emoji titles survive intact.
Feature-intact: genuinely hostile characters (path seps, control
chars, Windows-reserved) are still scrubbed.
"""

from __future__ import annotations

from endless_library.download import safe_filename


# ============ REGRESSION: Unicode letters survive ============


def test_bengali_title_preserved():
    """The exact title from the live queue (id=48): কাঁটায়-কাঁটায় ৬"""
    out = safe_filename("কাঁটায়-কাঁটায় ৬ -- Narayan Sanyal.pdf")
    assert "কাঁটায়-কাঁটায়" in out, f"Bengali dropped: {out!r}"
    assert "Narayan Sanyal" in out
    assert out.endswith(".pdf")


def test_chinese_japanese_korean_preserved():
    """CJK glyphs are the other common case for self-hosted users."""
    for text in ["三体.epub", "ノルウェイの森.epub", "사람 사는 일.epub"]:
        out = safe_filename(text)
        # First character must be the original Unicode glyph
        assert out[0] == text[0], f"CJK dropped for {text!r}: {out!r}"


def test_cyrillic_preserved():
    out = safe_filename("Война и мир -- Лев Толстой.epub")
    assert "Война" in out
    assert "Толстой" in out


def test_emoji_preserved():
    """Emoji isn't filesystem-hostile; rare but should not be silently lost."""
    out = safe_filename("Book Title 📚.epub")
    assert "📚" in out, f"emoji dropped: {out!r}"


def test_old_default_would_have_been_destroyed():
    """Documents WHY 54 files broke: with the old ASCII-only regex, a
    pure-Bengali title collapses to underscores. Pin the new behavior
    by inverting the assertion."""
    out = safe_filename("ফেলুদা সমগ্র ১, ২.epub")
    assert "ফেলুদা" in out
    # The old version would have produced something like "_ _ _ _.epub"
    assert "_ _" not in out
    assert out != ".epub"


# ============ FEATURE-INTACT: hostile chars still stripped ============


def test_forward_slash_replaced():
    """`/` is a path separator on POSIX; must NEVER appear in a filename."""
    out = safe_filename("Series/Volume 1 -- Author.epub")
    assert "/" not in out


def test_backslash_replaced():
    """Same on Windows."""
    out = safe_filename("Series\\\\Volume 1 -- Author.epub")
    assert "\\\\" not in out
    assert "\\" not in out


def test_windows_reserved_chars_replaced():
    """Drive-letter colon, glob wildcards, redirection chars."""
    out = safe_filename('Title: with "quotes" * and ? and | <pipes>.epub')
    for c in (':', '"', '*', '?', '|', '<', '>'):
        assert c not in out, f"{c!r} survived: {out!r}"


def test_nul_byte_replaced():
    out = safe_filename("Title\x00with\x01nul.epub")
    assert "\x00" not in out
    assert "\x01" not in out


def test_empty_or_dots_only_returns_book():
    """All-non-printable input must still produce a usable filename."""
    assert safe_filename("") == "book"
    assert safe_filename("....") == "book"
    assert safe_filename("///") == "book"


# ============ FEATURE-INTACT: length cap respects UTF-8 byte boundaries ============


def test_byte_length_cap_does_not_split_utf8():
    """A long Bengali title truncated at the byte boundary must NOT
    leave a partial multi-byte sequence (which would produce a
    replacement character or raise on subsequent decode)."""
    long_title = ("কাঁটায় " * 50) + ".pdf"
    out = safe_filename(long_title, max_length=120)
    # Must be valid UTF-8 round-trip
    out.encode("utf-8").decode("utf-8")
    # Must still end with the extension
    assert out.endswith(".pdf"), f"extension lost: {out!r}"
    # Must be at or under the byte cap
    assert len(out.encode("utf-8")) <= 120


def test_extension_preserved_on_truncation():
    """A long Latin name still preserves .epub at the end."""
    out = safe_filename("a" * 500 + ".epub", max_length=100)
    assert out.endswith(".epub")
    assert len(out.encode("utf-8")) <= 100


def test_short_unchanged():
    """Plain ASCII without hostile chars passes through verbatim
    (modulo NFC normalization, which is a no-op for ASCII)."""
    assert safe_filename("Foo Bar 2024.epub") == "Foo Bar 2024.epub"


def test_collapse_whitespace_runs():
    """Multiple spaces / underscores collapse into one space."""
    out = safe_filename("Title    with     spaces.epub")
    assert "    " not in out
    assert out == "Title with spaces.epub"


# ============ FEATURE-INTACT: NFC normalization ============


def test_nfc_normalization_applied():
    """Combining forms (NFD) normalize to NFC. Same visible glyph,
    fewer bytes, more interoperable on macOS HFS+ (which prefers NFD
    historically but Linux/Windows use NFC)."""
    import unicodedata
    # 'é' in NFD = 'e' + combining acute
    nfd = "Café.epub"
    nfc = "Café.epub"
    assert unicodedata.normalize("NFC", nfd) == nfc
    out_d = safe_filename(nfd)
    out_c = safe_filename(nfc)
    assert out_d == out_c == nfc
