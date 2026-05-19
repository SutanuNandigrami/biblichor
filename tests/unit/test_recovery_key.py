"""Regression + feature-intact tests for recovery_key.ensure_recovery_key
and the auto-encrypt-on-first-backup wiring (Phase 5c)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from endless_library.recovery_key import (
    RecoveryKey,
    RecoveryKeyError,
    _extract_public,
    ensure_recovery_key,
)


def _fake_keygen(out_path: Path) -> None:
    out_path.write_text(
        "# created: 2026-05-19T10:00:00Z\n"
        "# public key: age1examplepublickey9999999999999999999\n"
        "AGE-SECRET-KEY-1FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE\n"
    )


# ============ REGRESSION: idempotent generation + lockdown ============


def test_ensure_recovery_key_generates_when_absent(tmp_path, monkeypatch):
    """First call creates the file via age-keygen + chmods 0600 + returns
    just_generated=True."""
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/age-keygen")
    call_count = [0]

    def fake_run(cmd, **kw):
        call_count[0] += 1
        # Simulate age-keygen writing the output file
        out = Path(cmd[cmd.index("-o") + 1])
        _fake_keygen(out)

        class _Proc:
            returncode = 0
            stderr = ""
            stdout = ""

        return _Proc()

    monkeypatch.setattr("subprocess.run", fake_run)
    rk = ensure_recovery_key(tmp_path / "secrets")
    assert rk.just_generated is True
    assert rk.private_key_path.exists()
    assert rk.public_key == "age1examplepublickey9999999999999999999"
    assert call_count[0] == 1


def test_ensure_recovery_key_idempotent_second_call(tmp_path, monkeypatch):
    """Second call must NOT re-run age-keygen (which would replace the
    key + break every existing backup)."""
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/age-keygen")
    call_count = [0]

    def fake_run(cmd, **kw):
        call_count[0] += 1
        out = Path(cmd[cmd.index("-o") + 1])
        _fake_keygen(out)

        class _Proc:
            returncode = 0
            stderr = ""
            stdout = ""

        return _Proc()

    monkeypatch.setattr("subprocess.run", fake_run)

    first = ensure_recovery_key(tmp_path / "secrets")
    second = ensure_recovery_key(tmp_path / "secrets")
    assert call_count[0] == 1, "age-keygen ran twice — that would replace the key!"
    assert second.just_generated is False
    assert second.public_key == first.public_key


def test_ensure_recovery_key_chmods_600(tmp_path, monkeypatch):
    """The private key file MUST be mode 0600 — other local users
    should not be able to read it."""
    if os.name == "nt":
        pytest.skip("file modes don't work the same way on Windows")
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/age-keygen")

    def fake_run(cmd, **kw):
        out = Path(cmd[cmd.index("-o") + 1])
        _fake_keygen(out)
        # Simulate age-keygen creating with default umask (0644)
        os.chmod(out, 0o644)

        class _Proc:
            returncode = 0
            stderr = ""
            stdout = ""

        return _Proc()

    monkeypatch.setattr("subprocess.run", fake_run)

    rk = ensure_recovery_key(tmp_path / "secrets")
    mode = stat.S_IMODE(rk.private_key_path.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_ensure_recovery_key_raises_when_age_missing(tmp_path, monkeypatch):
    """Clear error if age-keygen isn't installed — don't silently fall
    through to unencrypted backups."""
    monkeypatch.setattr("shutil.which", lambda x: None)
    with pytest.raises(RecoveryKeyError, match="age-keygen not on PATH"):
        ensure_recovery_key(tmp_path / "secrets")


def test_ensure_recovery_key_raises_on_keygen_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/age-keygen")

    def fake_run(cmd, **kw):
        class _Proc:
            returncode = 1
            stderr = "permission denied"
            stdout = ""

        return _Proc()

    monkeypatch.setattr("subprocess.run", fake_run)
    with pytest.raises(RecoveryKeyError, match="age-keygen failed"):
        ensure_recovery_key(tmp_path / "secrets")


# ============ FEATURE-INTACT: public key extraction ============


def test_extract_public_handles_canonical_age_keygen_output(tmp_path):
    p = tmp_path / "k"
    _fake_keygen(p)
    assert _extract_public(p) == "age1examplepublickey9999999999999999999"


def test_extract_public_raises_when_line_missing(tmp_path):
    p = tmp_path / "k"
    p.write_text("# created: 2026-01-01\n# something else\nAGE-SECRET-KEY-1...\n")
    with pytest.raises(RecoveryKeyError, match="could not find public key"):
        _extract_public(p)


def test_recovery_key_dataclass_holds_expected_fields(tmp_path):
    """Pin the shape backup.py depends on."""
    rk = RecoveryKey(
        private_key_path=tmp_path / "k", public_key="age1xxx", just_generated=True
    )
    assert rk.public_key == "age1xxx"
    assert rk.just_generated is True
