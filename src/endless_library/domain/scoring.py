from __future__ import annotations

from rapidfuzz import fuzz

from endless_library.config import ScoringCfg
from endless_library.domain.models import Candidate, ScoreBreakdown, SearchQuery


def _normalize_title(t: str) -> str:
    return t.split(":")[0].strip().lower()


def _has_audio_marker(c: Candidate, audio_keywords: list[str]) -> bool:
    blob = " ".join([c.edition_hints or "", c.format or "", c.title or ""]).lower()
    return any(k in blob for k in audio_keywords)


def _is_non_latin(s: str) -> bool:
    """True iff `s` contains any Bengali, Devanagari, CJK, Arabic, Hebrew,
    Cyrillic, Greek, Korean, Thai, etc — i.e. anything that wouldn't share
    a single rapidfuzz token with a pure-ASCII Latin title.

    We pick this up via Unicode block ranges rather than `unicodedata.script`
    so we don't need an extra dep. Each tuple is (lo, hi) inclusive.
    """
    for ch in s:
        cp = ord(ch)
        # Latin Extended-A/B and accented Latin still count as "Latin-ish"
        if cp < 0x0250 or 0x1E00 <= cp <= 0x1EFF:
            continue
        # Anything in these blocks is definitely non-Latin script
        if (
            0x0370 <= cp <= 0x03FF  # Greek
            or 0x0400 <= cp <= 0x052F  # Cyrillic
            or 0x0590 <= cp <= 0x05FF  # Hebrew
            or 0x0600 <= cp <= 0x06FF  # Arabic
            or 0x0900 <= cp <= 0x097F  # Devanagari
            or 0x0980 <= cp <= 0x09FF  # Bengali
            or 0x0A00 <= cp <= 0x0A7F  # Gurmukhi
            or 0x0A80 <= cp <= 0x0AFF  # Gujarati
            or 0x0B00 <= cp <= 0x0B7F  # Oriya
            or 0x0B80 <= cp <= 0x0BFF  # Tamil
            or 0x0C00 <= cp <= 0x0C7F  # Telugu
            or 0x0C80 <= cp <= 0x0CFF  # Kannada
            or 0x0D00 <= cp <= 0x0D7F  # Malayalam
            or 0x0E00 <= cp <= 0x0E7F  # Thai
            or 0x0F00 <= cp <= 0x0FFF  # Tibetan
            or 0x1100 <= cp <= 0x11FF  # Hangul Jamo
            or 0x3040 <= cp <= 0x309F  # Hiragana
            or 0x30A0 <= cp <= 0x30FF  # Katakana
            or 0x3400 <= cp <= 0x4DBF  # CJK Ext A
            or 0x4E00 <= cp <= 0x9FFF  # CJK Unified
            or 0xAC00 <= cp <= 0xD7AF  # Hangul Syllables
        ):
            return True
    return False


def _non_latin_substring(s: str) -> str:
    """Pull just the non-Latin glyphs (plus whitespace and digits) out of `s`.

    `Kantai Kantai 6 (কাঁটায় কাঁটায়-৬)` → ` 6  কাঁটায় কাঁটায়-৬ `.
    Used to neutralize transliteration noise before fuzz-matching same-script
    titles — rapidfuzz then sees Bengali-vs-Bengali only.
    """
    out: list[str] = []
    for ch in s:
        cp = ord(ch)
        # Keep whitespace, digits, and obvious punctuation that titles share
        if ch.isspace() or ch in "-–—_·:.0123456789":  # noqa: RUF001
            out.append(ch)
            continue
        # Skip pure Latin (incl. accented Latin) — that's the transliteration
        if cp < 0x0250 or 0x1E00 <= cp <= 0x1EFF:
            continue
        out.append(ch)
    return "".join(out).strip()


def _author_match_strict(q_author_lc: str, haystack: str) -> float:
    """Author fallback when the candidate row has no parsed author.

    The previous heuristic used `partial_token_set_ratio(q_author, haystack)`
    with a 0.7 floor, which spuriously credited a single shared surname like
    "Narayan" against any Indian book mentioning "narayan". Now we require
    every meaningful (>2-char) token of the queried author to appear in the
    haystack — full last+first match or full last+initial — or zero credit.
    """
    q_tokens = [t for t in q_author_lc.split() if len(t) > 2]
    if not q_tokens:
        return 0.0
    hits = sum(1 for t in q_tokens if t in haystack)
    return 1.0 if hits == len(q_tokens) else 0.0


def score_candidate(
    c: Candidate,
    q: SearchQuery,
    cfg: ScoringCfg,
    *,
    isbn13_match: bool | None = None,
) -> ScoreBreakdown:
    components: dict[str, float] = {}

    if _has_audio_marker(c, cfg.audio_keywords):
        return ScoreBreakdown(
            total=0.0,
            components={"audio_hard_skip": 0.0},
            is_hard_skip=True,
            skip_reason="audio",
        )
    # Hard-skip cheap derivative content: summaries, study guides, conversation
    # starters (bbrown430-inspired filter).
    derivative_terms = (
        "summary",
        "summaries",
        "conversation starter",
        "study guide",
        "book club",
        "a guide to",
    )
    blob = " ".join([(c.title or ""), (c.edition_hints or "")]).lower()
    for term in derivative_terms:
        if term in blob:
            return ScoreBreakdown(
                total=0.0,
                components={"derivative_hard_skip": 0.0},
                is_hard_skip=True,
                skip_reason=f"derivative ({term})",
            )

    # Script-mismatch hard-skip. If the queried title is in a non-Latin
    # script (Bengali, Devanagari, CJK, etc) and the candidate title contains
    # no non-Latin glyphs, the candidate cannot possibly be the book — Anna's
    # just returned an English fallback because we hinted lang=en.
    if q.title and c.title:
        q_non_latin = _is_non_latin(q.title)
        c_non_latin = _is_non_latin(c.title)
        if q_non_latin != c_non_latin:
            return ScoreBreakdown(
                total=0.0,
                components={"script_mismatch_hard_skip": 0.0},
                is_hard_skip=True,
                skip_reason="script_mismatch",
            )

    # ISBN — caller is the authoritative source of truth via isbn13_match
    # Fallback: peek at raw["isbns"] list if present
    if isbn13_match is None:
        isbns = (c.raw or {}).get("isbns") or []
        isbn_hit = bool(q.isbn13) and q.isbn13 in isbns
    else:
        isbn_hit = isbn13_match
    components["isbn_match"] = cfg.isbn_match if isbn_hit else 0.0

    # Title
    t_sim = 0.0
    title_weight_eff = cfg.title_weight
    if c.title and q.title:
        t_norm_q = _normalize_title(q.title)
        t_norm_c = _normalize_title(c.title)
        t_sim = fuzz.token_set_ratio(t_norm_c, t_norm_q) / 100.0

        # If both sides have non-Latin glyphs, also try the non-Latin
        # substring of each — this neutralizes transliteration padding on
        # the candidate side (e.g. "Kantai Kantai 6 (কাঁটায় কাঁটায়-৬)"
        # vs "কাঁটায়-কাঁটায় ৬") and gives us a more honest similarity
        # score. We take the max of the two ratios.
        if _is_non_latin(q.title) and _is_non_latin(c.title):
            sub_q = _non_latin_substring(t_norm_q)
            sub_c = _non_latin_substring(t_norm_c)
            if sub_q and sub_c:
                t_sim_sub = fuzz.token_set_ratio(sub_c, sub_q) / 100.0
                t_sim = max(t_sim, t_sim_sub)
            title_weight_eff = cfg.title_weight * cfg.non_latin_title_multiplier
    components["title_similarity"] = t_sim * title_weight_eff

    # Author (with row-text fallback when parser couldn't pull author cleanly)
    a_sim = 0.0
    if q.author:
        q_author_lc = q.author.lower()
        if c.author:
            a_sim = fuzz.token_set_ratio(c.author.lower(), q_author_lc) / 100.0
        else:
            haystack = " ".join(
                [
                    c.edition_hints or "",
                    str((c.raw or {}).get("row_text") or ""),
                ]
            ).lower()
            a_sim = _author_match_strict(q_author_lc, haystack)
    components["author_similarity"] = a_sim * cfg.author_weight

    # Format bonus
    fmt = (c.format or "").lower()
    components["format_bonus"] = float(cfg.format_bonus.get(fmt, 0))

    # Language
    components["language_bonus"] = (
        cfg.language_bonus if (c.language or "").lower() == q.language.lower() else 0.0
    )

    # Filesize
    if c.filesize_bytes is None:
        components["filesize_penalty"] = 0.0
    elif c.filesize_bytes < 100_000:
        components["filesize_penalty"] = -15.0
    elif cfg.filesize_min_bytes <= c.filesize_bytes <= cfg.filesize_max_bytes:
        components["filesize_penalty"] = 5.0
    else:
        components["filesize_penalty"] = 0.0

    # Scan/OCR penalty
    hints = (c.edition_hints or "").lower()
    components["scan_penalty"] = (
        -cfg.scan_penalty if any(k in hints for k in ("scan", "ocr")) else 0.0
    )

    total = sum(components.values())
    total = max(0.0, min(100.0, total))
    return ScoreBreakdown(total=total, components=components, is_hard_skip=False)
