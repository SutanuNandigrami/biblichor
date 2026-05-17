from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_cli_help():
    r = subprocess.run(
        [sys.executable, "-m", "endless_library", "--help"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "endless-library" in r.stdout
    assert "run-once" in r.stdout
    assert "bench" in r.stdout


def test_cli_status_empty(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg_path = cfg_dir / "config.yaml"
    cfg_path.write_text(
        (Path(__file__).resolve().parents[2] / "config" / "config.yaml.example").read_text()
    )
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "endless_library",
            "--config",
            str(cfg_path),
            "--db",
            str(tmp_path / "library.db"),
            "status",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "queue status" in r.stdout
