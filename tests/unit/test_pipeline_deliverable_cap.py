"""Regression tests for _compute_deliverable_cap.

Before this fix, candidates >17MB were hard-skipped at scoring even
when STK delivery (which supports Amazon's 200MB per-file limit)
was fully configured. User report: "why this is hardskipped when
amazon web upload supports 200mb" — a 111MB Bengali PDF showed as
"Hard-skipped: oversize (111MB > 17MB cap)" despite the existing
oversize-routed-stk path being available.

The cap calculation now considers BOTH paths and returns the larger.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from endless_library.config import Config
from endless_library.pipeline import _compute_deliverable_cap


def _make_deps(cfg: Config, stk_configured: bool) -> SimpleNamespace:
    """Tiny stand-in for PipelineDeps. Only `.cfg` and
    `.bookorbit_service` are actually read by the helper."""
    return SimpleNamespace(
        cfg=cfg,
        bookorbit_service=MagicMock(),
    )


def _patch_stk(configured: bool):
    """Patch KindleStkService(...).is_configured() globally."""
    mock_instance = MagicMock()
    mock_instance.is_configured.return_value = configured
    return patch(
        "endless_library.kindle_stk.KindleStkService",
        return_value=mock_instance,
    )


def test_smtp_only_cap_is_smtp_divided_by_base64_overhead():
    """No STK: cap = SMTP attachment cap / 1.4 (base64 overhead)."""
    cfg = Config()
    cfg.smtp.max_attachment_mb = 24
    deps = _make_deps(cfg, stk_configured=False)
    with _patch_stk(False):
        cap = _compute_deliverable_cap(deps)
    expected = int(24 * 1024 * 1024 / 1.4)
    assert cap == expected
    # Sanity: roughly 17 MB
    assert 16 * 1024 * 1024 < cap < 18 * 1024 * 1024


def test_stk_configured_lifts_cap_to_200mb():
    """STK configured: cap = STK per-file limit (200MB by default),
    NOT the SMTP cap. A 111MB PDF must pass the gate."""
    cfg = Config()
    cfg.smtp.max_attachment_mb = 24
    deps = _make_deps(cfg, stk_configured=True)
    with _patch_stk(True):
        cap = _compute_deliverable_cap(deps)
    assert cap == 200 * 1024 * 1024
    # The user's actual book: 111.5 MB
    assert cap > 111 * 1024 * 1024


def test_stk_configured_with_large_smtp_keeps_max():
    """If SMTP cap somehow exceeds STK cap (hypothetical big-attachment
    relay), still return the larger — never shrink."""
    cfg = Config()
    cfg.smtp.max_attachment_mb = 500  # 500MB SMTP cap (absurd, but possible)
    deps = _make_deps(cfg, stk_configured=True)
    with _patch_stk(True):
        cap = _compute_deliverable_cap(deps)
    smtp_cap = int(500 * 1024 * 1024 / 1.4)
    assert cap == max(smtp_cap, 200 * 1024 * 1024)
    assert cap == smtp_cap  # ~357 MB > 200 MB


def test_stk_check_failure_falls_back_to_smtp_cap():
    """If is_configured() raises, treat STK as unavailable rather than
    crashing the pipeline."""
    cfg = Config()
    cfg.smtp.max_attachment_mb = 24
    deps = _make_deps(cfg, stk_configured=False)

    mock_instance = MagicMock()
    mock_instance.is_configured.side_effect = RuntimeError("secrets store down")
    with patch(
        "endless_library.kindle_stk.KindleStkService",
        return_value=mock_instance,
    ):
        cap = _compute_deliverable_cap(deps)
    # Falls back to SMTP-only cap; doesn't raise.
    assert cap == int(24 * 1024 * 1024 / 1.4)


@pytest.mark.parametrize("size_mb,expected_skip", [
    (10, False),     # well under either cap
    (15, False),     # under SMTP cap
    (50, True),      # over SMTP cap; would skip without STK
    (111, True),     # the user's actual case
    (199, True),     # just under STK cap
])
def test_documents_thresholds(size_mb, expected_skip):
    """Document the matrix: what gets hard-skipped without STK vs with.
    `expected_skip` is for the SMTP-only world (regression baseline)."""
    cfg = Config()
    cfg.smtp.max_attachment_mb = 24
    deps_smtp_only = _make_deps(cfg, stk_configured=False)
    deps_with_stk = _make_deps(cfg, stk_configured=True)

    bytes_ = size_mb * 1024 * 1024

    with _patch_stk(False):
        smtp_cap = _compute_deliverable_cap(deps_smtp_only)
    with _patch_stk(True):
        stk_cap = _compute_deliverable_cap(deps_with_stk)

    # SMTP-only world matches the baseline expectation.
    assert (bytes_ > smtp_cap) == expected_skip
    # STK world: NONE of these (up to 199 MB) should be hard-skipped.
    assert bytes_ <= stk_cap
