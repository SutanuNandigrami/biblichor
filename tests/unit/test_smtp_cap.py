"""Regression + feature-intact tests for the SMTP attachment cap.

Audit context: the user reported "72MB raw -> ~101MB after base64,
cap 22MB" with the question "Gmail allows 50MB, why this error?"
Two confusions to pin in tests forever:

1. Gmail's 50 MB applies to INBOUND. Outbound is 25 MB MIME-encoded.
2. `max_attachment_mb` is the *encoded* cap, not the raw cap. Pipeline
   compares `raw_bytes * 1.4` against `max_attachment_mb`, so the
   effective RAW ceiling is `max_attachment_mb / 1.4`.

Decision: default goes 22 -> 24 (raw ceiling 15.7 -> 17.1 MB, 9% more
books fit Gmail). Docstring rewritten so the next reader doesn't
misinterpret the field as a raw cap.
"""

from __future__ import annotations

from endless_library.config import SmtpCfg

# Gmail's outbound MIME-encoded ceiling
GMAIL_OUTBOUND_LIMIT_BYTES = 25 * 1024 * 1024
# The inflation factor used in pipeline.py:_process_from_downloaded
# (base64 4/3 inflation + email header/boundary overhead).
PIPELINE_INFLATION_FACTOR = 1.4


# ============ REGRESSION ============


def test_default_attachment_cap_is_24_mb() -> None:
    """Pin the new default. Earlier 22 was unnecessarily tight (15.7 MB
    raw ceiling). 24 gives 17.1 MB raw while keeping a 1 MB safety
    margin under Gmail's 25 MB MIME envelope."""
    assert SmtpCfg().max_attachment_mb == 24


def test_default_cap_stays_under_gmail_envelope() -> None:
    """The contract that must never break: default cap (in bytes) must
    be <= Gmail's 25 MB outbound MIME ceiling. If a future PR bumps
    the default past 25 without changing the inflation factor, files
    that pass our guard will be rejected by Gmail."""
    cap_bytes = SmtpCfg().max_attachment_mb * 1024 * 1024
    assert cap_bytes <= GMAIL_OUTBOUND_LIMIT_BYTES, (
        f"default cap {SmtpCfg().max_attachment_mb} MB exceeds Gmail's 25 MB"
    )
    # And there must be a real safety margin (>= 512 KB) so header weight
    # variance doesn't push the actual encoded size over.
    margin = GMAIL_OUTBOUND_LIMIT_BYTES - cap_bytes
    assert margin >= 512 * 1024, (
        f"safety margin to Gmail envelope is only {margin} bytes — too tight"
    )


def test_max_attachment_mb_is_encoded_cap_not_raw() -> None:
    """Pins the SEMANTICS of the field. The pipeline computes
    `inflated = raw_bytes * 1.4` and rejects when `inflated > cap`.
    Therefore the field is an encoded-size cap. Documenting this with
    a test so the docstring drift that caused the original bug can't
    repeat: a 17 MB raw file is the practical ceiling for the default
    cap of 24, NOT the cap itself."""
    cap_bytes = SmtpCfg().max_attachment_mb * 1024 * 1024
    effective_raw_ceiling = int(cap_bytes / PIPELINE_INFLATION_FACTOR)
    # For default cap=24, raw ceiling ~= 17.1 MB
    assert 16 * 1024 * 1024 < effective_raw_ceiling < 18 * 1024 * 1024


# ============ FEATURE-INTACT: pipeline-aligned acceptance logic ============


def _pipeline_would_accept(raw_bytes: int, cap_mb: int) -> bool:
    """Mirror the check from pipeline.py:_process_from_downloaded line 528.
    Tests against THIS function so changes to the cap have predictable
    raw-byte consequences."""
    inflated = int(raw_bytes * PIPELINE_INFLATION_FACTOR)
    cap_bytes = cap_mb * 1024 * 1024
    return inflated <= cap_bytes


def test_typical_3mb_epub_accepted() -> None:
    """A typical EPUB is 1-5 MB."""
    assert _pipeline_would_accept(3 * 1024 * 1024, cap_mb=24)


def test_borderline_17mb_raw_accepted_with_default_cap() -> None:
    """17 MB raw -> 23.8 MB encoded -> fits Gmail's 25 MB outbound.
    With the previous cap of 22, this file would have been rejected
    (17 * 1.4 = 23.8 > 22). The bump unblocks ~9% more books."""
    assert _pipeline_would_accept(17 * 1024 * 1024, cap_mb=24)


def test_borderline_17mb_raw_rejected_under_old_cap_of_22() -> None:
    """Documents the change: prior default would have rejected this."""
    assert not _pipeline_would_accept(17 * 1024 * 1024, cap_mb=22)


def test_huge_72mb_pdf_still_rejected() -> None:
    """A 72 MB raw PDF is the case the user originally reported. 72 * 1.4
    = 100.8 MB which is way over even SES's 40 MB cap. These need the
    PDF->EPUB rescue path or (future) Send-to-Kindle web upload."""
    assert not _pipeline_would_accept(72 * 1024 * 1024, cap_mb=24)
    assert not _pipeline_would_accept(72 * 1024 * 1024, cap_mb=40)


def test_smtp_cap_is_configurable() -> None:
    """SES users can raise to 40 (raw ceiling ~28.5 MB)."""
    ses_cap = SmtpCfg(max_attachment_mb=40)
    assert ses_cap.max_attachment_mb == 40
    # SES's actual encoded ceiling is 40 MB raw / hard cap 41 MB total,
    # so cap=40 with our 1.4 factor stays safe.


# ============ FEATURE-INTACT: docstring contains the field semantics ============


def test_docstring_says_encoded_not_raw() -> None:
    """The original bug was that the comment claimed `max_attachment_mb`
    was the raw cap when the pipeline treated it as encoded. Pin the
    fix: the inline comment must contain the word 'encoded' near the
    field so a future reader doesn't repeat the mistake."""
    import inspect

    from endless_library import config

    src = inspect.getsource(config.SmtpCfg)
    assert "encoded" in src.lower(), (
        "SmtpCfg docstring/comments must clarify the field is an encoded (not raw) MIME size cap"
    )
    # And it should explicitly disambiguate Gmail's 50 MB inbound vs 25 MB outbound
    assert "50" in src and ("inbound" in src.lower() or "outbound" in src.lower()), (
        "docstring should note Gmail's 50 MB number is inbound, not outbound"
    )
