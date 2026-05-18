"""Thin wrapper around the `clamscan` binary.

We shell out instead of using `pyclamd` so:
  - Zero new Python deps
  - Works whether ClamAV is installed via apt, brew, or a custom build
  - The daemon (clamd) doesn't need to be running — `clamscan` self-loads
    the signature DB (slower per-call but bulletproof for a low-volume
    pipeline)

The pipeline calls `scan(path, require=...)` after unpacking and treats
the return value:

  ScanResult(ok=True, ...)        → file passes; continue convert/send
  ScanResult(ok=False, infected)  → fail the book, hard-stop
  ScanResult(ok=True, skipped)    → ClamAV not installed and `require=False`;
                                    we already enforced extraction hygiene,
                                    so allow with a warning. The Settings
                                    page surfaces a banner when this happens.

When `require=True` and ClamAV is missing, we return ok=False to abort.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScanResult:
    ok: bool
    skipped: bool = False  # True iff ClamAV not present and not required
    threat: str | None = None  # populated when an actual hit is detected
    detail: str | None = None  # error text for skipped/unknown cases


def is_installed() -> bool:
    return shutil.which("clamscan") is not None


def scan(path: Path, *, require: bool = False, timeout_seconds: int = 60) -> ScanResult:
    """Run clamscan on `path`.

    `require=True`  → ClamAV missing is a hard fail.
    `require=False` → ClamAV missing is a graceful skip (returns ok=True,
                      skipped=True). An *infected* file is always a fail,
                      even when require=False.
    """
    if not is_installed():
        if require:
            return ScanResult(
                ok=False,
                detail="ClamAV (clamscan) is not installed but cfg.security.require_clamav=True",
            )
        log.warning(
            "ClamAV not installed; allowing %s after hygiene checks only. "
            "Install with `sudo apt install clamav clamav-daemon` to enable scanning.",
            path.name,
        )
        return ScanResult(ok=True, skipped=True, detail="clamscan not installed")

    cmd = ["clamscan", "--no-summary", "--infected", "--stdout", str(path)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ScanResult(ok=False, detail=f"clamscan timed out after {timeout_seconds}s")
    except OSError as e:
        return ScanResult(ok=False, detail=f"clamscan failed to spawn: {e}")

    # Exit codes per clamscan(1):
    #   0 = clean
    #   1 = infected
    #   2 = error (DB load failure, etc) — treat as hard fail
    if proc.returncode == 0:
        return ScanResult(ok=True, detail="clean")
    if proc.returncode == 1:
        # stdout looks like:  "/path/to/file: Win.Trojan.Foo FOUND"
        threat = _parse_threat(proc.stdout)
        return ScanResult(ok=False, threat=threat, detail=proc.stdout.strip()[:200])
    return ScanResult(
        ok=False,
        detail=f"clamscan returned {proc.returncode}: {proc.stderr.strip()[:200]}",
    )


def _parse_threat(stdout: str) -> str | None:
    for line in stdout.splitlines():
        if "FOUND" in line:
            # Example: "/data/books/book.epub: Win.Trojan.Foo FOUND"
            parts = line.rsplit(":", 1)
            if len(parts) == 2:
                tail = parts[1].strip()
                if tail.endswith(" FOUND"):
                    return tail[: -len(" FOUND")].strip()
            return line.strip()
    return None
