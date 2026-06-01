"""Latin → native-script query rewriting for non-English book search.

WHY: Anna's full-text index matches the catalog's native-script titles
literally — `রবীন্দ্রনাথ` returns 19 Bengali books, but the imperfect
ITRANS form `রবিন্দ্রনথ্` returns zero. Naive transliteration via
`indic-transliteration` produces forms Anna's tokenizer rejects.

STRATEGY: a hand-curated map of common authors/topics for each
supported language, grounded in live Anna's matches (2026-06-02).
For Latin tokens NOT in the map, fall back to indic-transliteration
as best-effort — won't always match but never makes things worse.

The map only contains entries whose native form returned ≥1 Bengali-
script result in live testing. Anything that returned zero (e.g.
naive `ঠাকুর` alone, `রামায়ণ`) is omitted so we don't ship dead-ends.
"""
from __future__ import annotations

# Latin token / phrase → native script.
# Single tokens checked after phrase matching; phrases (with space) take
# precedence over per-token substitution.
_BENGALI: dict[str, str] = {
    # Multi-word phrases (longest first via len-sort at lookup time)
    "kriya yoga": "ক্রিয়া যোগ",
    "kazi nazrul": "কাজী নজরুল",
    "rabindranath tagore": "রবীন্দ্রনাথ ঠাকুর",
    "satyajit ray": "সত্যজিৎ রায়",
    "sukumar ray": "সুকুমার রায়",
    "humayun ahmed": "হুমায়ুন আহমেদ",
    "sunil gangopadhyay": "সুনীল গঙ্গোপাধ্যায়",
    "sarat chandra": "শরৎচন্দ্র",
    "sharat chandra": "শরৎচন্দ্র",
    # Single tokens (Bengali authors)
    "rabindranath": "রবীন্দ্রনাথ",
    "nazrul": "নজরুল",
    "saratchandra": "শরৎচন্দ্র",
    "bibhutibhushan": "বিভূতিভূষণ",
    "satyajit": "সত্যজিৎ",
    "sukumar": "সুকুমার",
    "sunil": "সুনীল",
    "humayun": "হুমায়ুন",
    "yogananda": "পরমহংস যোগানন্দ",
    "paramahansa yogananda": "পরমহংস যোগানন্দ",
    "ramakrishna": "রামকৃষ্ণ",
    "vivekananda": "বিবেকানন্দ",
    "swami vivekananda": "স্বামী বিবেকানন্দ",
    # Single tokens (Bengali concepts / topics)
    "kriya": "ক্রিয়া",
    "yoga": "যোগ",
    "vedanta": "বেদান্ত",
    "gita": "গীতা",
    "mahabharata": "মহাভারত",
    "bengali": "বাংলা",
    "bangla": "বাংলা",
    "kolkata": "কলকাতা",
    "calcutta": "কলকাতা",
}

# Lang -> map. Add more languages as ground-truthed against Anna's.
_MAPS: dict[str, dict[str, str]] = {
    "bn": _BENGALI,
}


def _substitute_phrases(q: str, lang_map: dict[str, str]) -> tuple[str, int]:
    """Greedy longest-match phrase substitution. Returns (rewritten, hits)."""
    phrases = sorted(
        (k for k in lang_map if " " in k), key=lambda p: -len(p)
    )
    out = q
    hits = 0
    for p in phrases:
        if p in out:
            out = out.replace(p, lang_map[p])
            hits += 1
    return out, hits


def _substitute_tokens(q: str, lang_map: dict[str, str]) -> tuple[str, int]:
    """Per-token substitution for tokens that didn't get caught by phrases.
    Only replaces tokens that are pure-Latin (skip already-substituted text)."""
    parts = q.split(" ")
    out_parts: list[str] = []
    hits = 0
    for tok in parts:
        low = tok.lower().strip(".,;:!?\"'()[]{}")
        if low and low in lang_map and all(ord(c) < 0x024F for c in tok):
            out_parts.append(lang_map[low])
            hits += 1
        else:
            out_parts.append(tok)
    return " ".join(out_parts), hits


def _fallback_transliteration(q: str, lang: str) -> str | None:
    """Best-effort transliteration via indic-transliteration for tokens
    we don't have curated. Returns None if the library isn't installed
    or the lang isn't supported."""
    try:
        from indic_transliteration import sanscript
        from indic_transliteration.sanscript import transliterate
    except ImportError:
        return None
    scheme_map = {
        "bn": sanscript.BENGALI,
        "hi": sanscript.DEVANAGARI,
        "ta": sanscript.TAMIL,
        "te": sanscript.TELUGU,
        "ml": sanscript.MALAYALAM,
        "gu": sanscript.GUJARATI,
        "pa": sanscript.GURMUKHI,
    }
    target = scheme_map.get(lang)
    if target is None:
        return None
    try:
        return transliterate(q.lower(), sanscript.ITRANS, target)
    except Exception:
        return None


def transliterate_query(query: str, lang: str) -> str | None:
    """Rewrite a Latin-script query into the language's native script.

    Returns the rewritten query if at least one substitution fired,
    otherwise returns the fallback transliteration if available,
    otherwise None.

    Caller should treat None as 'skip this dual-query path'.
    """
    if not query or not query.strip():
        return None
    lang_map = _MAPS.get(lang)
    if lang_map is None:
        # No curated map for this language — try fallback only.
        return _fallback_transliteration(query, lang)

    # Skip rewrite if the user already typed native script.
    if any(ord(c) > 0x024F for c in query):
        return None

    q_low = query.lower().strip()
    rewritten, phrase_hits = _substitute_phrases(q_low, lang_map)
    rewritten, tok_hits = _substitute_tokens(rewritten, lang_map)
    total_hits = phrase_hits + tok_hits
    if total_hits == 0:
        # Nothing in the curated map matched — try the imperfect fallback
        # as last resort. May still find SOME results via partial matches.
        return _fallback_transliteration(query, lang)
    # If only some tokens got substituted, the rest stay Latin — that's
    # fine; Anna's tokenizes on whitespace and matches the substituted
    # parts independently.
    return rewritten
