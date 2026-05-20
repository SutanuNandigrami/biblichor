"""Updated transition tests + regression tests for the audit-aligned _LEGAL.

The audit found _LEGAL claimed `sending → {sent, failed}` only, but
_process_from_downloaded transitions `sending → downloading` (PDF→EPUB
rescue) and `sending → needs_review` (SMTP size guard). It also missed
the `queued` destinations from terminal states that the /retry button
actually performs via reset_for_research.

The table is now aligned with the pipeline. These tests pin the new
contract AND prove the pipeline's real transitions are now legal.
"""

from __future__ import annotations

import pytest

from endless_library.domain.state_machine import STATES, decide_auto_pick, is_legal_transition

# ============ REGRESSION: previously-missing transitions are now legal ============


@pytest.mark.parametrize(
    "frm,to",
    [
        # PDF→EPUB rescue mid-send: this was the audit's #1 finding
        ("sending", "downloading"),
        # SMTP size guard demoting at the last stage
        ("sending", "needs_review"),
        # /retry button → reset_for_research can re-queue ANY non-flight state
        ("needs_review", "queued"),
        ("failed", "queued"),
        ("sent", "queued"),
        ("skipped", "queued"),
        # /sources → manual skip path
        ("searching", "skipped"),
    ],
)
def test_audit_aligned_transitions_now_legal(frm: str, to: str) -> None:
    assert is_legal_transition(frm, to), (
        f"transition {frm} → {to} is performed by pipeline but _LEGAL still rejects it"
    )


# ============ FEATURE-INTACT: original legal transitions still legal ============


@pytest.mark.parametrize(
    "frm,to",
    [
        ("queued", "searching"),
        ("searching", "needs_review"),
        ("searching", "downloading"),
        ("searching", "failed"),
        ("downloading", "converting"),
        ("downloading", "sending"),
        ("downloading", "failed"),
        ("converting", "sending"),
        ("converting", "failed"),
        ("sending", "sent"),
        ("sending", "failed"),
        ("failed", "searching"),
        ("failed", "skipped"),
        ("needs_review", "downloading"),
        ("needs_review", "skipped"),
    ],
)
def test_pre_existing_transitions_still_legal(frm: str, to: str) -> None:
    assert is_legal_transition(frm, to) is True


# ============ FEATURE-INTACT: actually-illegal transitions still illegal ============


@pytest.mark.parametrize(
    "frm,to",
    [
        # Can't queue a book straight to sent
        ("queued", "sent"),
        # Can't queue mid-download
        ("downloading", "queued"),
        # Can't go backwards from a terminal to a flight state
        ("sent", "searching"),
        ("sent", "downloading"),
        ("skipped", "downloading"),
        # Made-up state
        ("invented_state", "queued"),
    ],
)
def test_truly_illegal_transitions_still_rejected(frm: str, to: str) -> None:
    assert is_legal_transition(frm, to) is False


def test_states_known() -> None:
    """Sanity — the STATES tuple hasn't drifted."""
    assert STATES == (
        "queued",
        "searching",
        "needs_review",
        "downloading",
        "converting",
        "sending",
        "sent",
        "skipped",
        "failed",
    )


# ============ FEATURE-INTACT: decide_auto_pick unchanged ============


def test_auto_pick_thresholds() -> None:
    """Existing auto-pick contract — same as before the _LEGAL refactor."""
    assert decide_auto_pick(top=75, second=60, threshold=70, gap=10) == "auto"
    assert decide_auto_pick(top=35, second=10, threshold=70, gap=10) == "failed"
    assert decide_auto_pick(top=65, second=10, threshold=70, gap=10) == "needs_review"
    assert decide_auto_pick(top=70, second=0, threshold=70, gap=10) == "auto"


def test_auto_pick_high_confidence_overrides_gap() -> None:
    """Bonus rule unchanged."""
    assert decide_auto_pick(top=80, second=80, threshold=70, gap=10) == "auto"
    assert decide_auto_pick(top=70, second=70, threshold=70, gap=5) == "needs_review"
    assert (
        decide_auto_pick(top=85, second=85, threshold=70, gap=5, high_confidence_bonus=10) == "auto"
    )
    assert (
        decide_auto_pick(top=85, second=85, threshold=70, gap=5, high_confidence_bonus=20)
        == "needs_review"
    )


# ============ Models: orphan Literal entries removed ============


def test_provider_literal_only_lists_implemented_scrapers() -> None:
    """Cleanup verification: `ebanglalibrary` and `zlib` had been in the
    Candidate.provider Literal even though neither scraper exists. Plain
    dataclasses don't enforce Literal at runtime, but the type
    annotation is still important documentation. Inspect it directly."""
    from typing import get_args, get_type_hints

    from endless_library.domain.models import Candidate

    hints = get_type_hints(Candidate)
    provider_values = set(get_args(hints["provider"]))
    assert provider_values == {
        "annas",
        "welib",
        "libgen",
        "archive",
        "kindlebangla",
        # Phase 6s.1 — public-domain curated sources
        "gutendex",
        "standard_ebooks",
        "oapen",
        "doab",
        "wikisource",
    }
    assert "ebanglalibrary" not in provider_values
    assert "zlib" not in provider_values


def test_candidate_accepts_all_registered_providers() -> None:
    """Every value remaining in the Literal must be an actual scraper
    that ships in the registry."""
    from endless_library.domain.models import Candidate
    from endless_library.scrapers import registry as r

    registered_providers = set()
    for name in r.available():
        # Build a sentinel + read its `provider` attribute
        try:
            scraper_cls = r._REGISTRY[name]
            registered_providers.add(scraper_cls.provider)
        except Exception:
            pass

    # Every Literal value must round-trip Pydantic + match a real scraper
    for provider in ("annas", "welib", "libgen", "archive", "kindlebangla"):
        # round-trip Pydantic
        c = Candidate(
            provider=provider,
            md5=None,
            title="x",
            author=None,
            language=None,
            format=None,
            filesize_bytes=None,
            year=None,
            publisher=None,
            edition_hints="",
            detail_url="",
        )
        assert c.provider == provider
        # match a real scraper
        assert provider in registered_providers, (
            f"Literal includes {provider!r} but no scraper class has provider = {provider!r}"
        )
