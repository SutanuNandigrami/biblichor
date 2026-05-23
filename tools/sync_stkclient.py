#!/usr/bin/env python3
"""Pull a fresh copy of stkclient into kindle_stk/_vendored/.

Usage:
    python tools/sync_stkclient.py             # uses the pinned version
    python tools/sync_stkclient.py --tag X.Y   # explicit tag
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

PINNED_TAG = "v0.1.1"
REPO_URL = "https://github.com/maxdjohnson/stkclient.git"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tag", default=PINNED_TAG)
    args = p.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    vendored = project_root / "src" / "endless_library" / "kindle_stk" / "_vendored"
    tmp = Path("/tmp/stkclient_sync")

    if tmp.exists():
        shutil.rmtree(tmp)
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", args.tag, REPO_URL, str(tmp)],
        check=True,
    )
    # upstream uses src/stkclient layout
    src = tmp / "src" / "stkclient"
    if not src.is_dir():
        # fallback to flat layout
        src = tmp / "stkclient"
    if not src.is_dir():
        print(f"FATAL: stkclient package not found in upstream clone", file=sys.stderr)
        return 1
    for f in src.glob("*.py"):
        if f.name in ("__init__.py", "__main__.py"):
            continue  # biblichor's __init__.py is hand-written; __main__.py not needed
        dest = vendored / f"_{f.name}"
        shutil.copyfile(f, dest)
        text = dest.read_text(encoding="utf-8")
        text = re.sub(r"^from stkclient\.model import", "from ._model import", text, flags=re.M)
        text = re.sub(r"^from stkclient\.signer import", "from ._signer import", text, flags=re.M)
        text = re.sub(r"^from stkclient\.", "from ._", text, flags=re.M)
        dest.write_text(text, encoding="utf-8")
        print(f"Synced {dest.name}")
    # Also sync __init__.py as _stkclient.py
    init_src = src / "__init__.py"
    if init_src.exists():
        dest = vendored / "_stkclient.py"
        shutil.copyfile(init_src, dest)
        text = dest.read_text(encoding="utf-8")
        text = re.sub(
            r"^from stkclient import api, model, signer",
            "from . import _api as api, _model as model, _signer as signer",
            text,
            flags=re.M,
        )
        dest.write_text(text, encoding="utf-8")
        print(f"Synced _stkclient.py")
    print(f"Done. Verify with: python -m pytest tests/unit/test_kindle_stk_*.py -v")
    return 0


if __name__ == "__main__":
    sys.exit(main())
