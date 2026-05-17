from __future__ import annotations

from rapidfuzz import fuzz

from endless_library.config import ScoringCfg
from endless_library.domain.models import Candidate, ScoreBreakdown, SearchQuery


def _normalize_title(t: str) -> str:
    return t.split(":")[0].strip().lower()


def _has_audio_marker(c: Candidate, audio_keywords: list[str]) -> bool:
    blob = " ".join([c.edition_hints or "", c.format or "", c.title or ""]).lower()
    return any(k in blob for k in audio_keywords)


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
    if c.title and q.title:
        t_sim = fuzz.token_set_ratio(_normalize_title(c.title), _normalize_title(q.title)) / 100.0
    components["title_similarity"] = t_sim * cfg.title_weight

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
            if haystack:
                # token_set_ratio is good for "Angie Thomas" anywhere in a long string
                a_sim = fuzz.partial_token_set_ratio(q_author_lc, haystack) / 100.0
                # Don't credit weak partial matches (single-token coincidence)
                if a_sim < 0.7:
                    a_sim = 0.0
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
