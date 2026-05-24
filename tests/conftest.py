from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))  # allows 'from tests._stkclient_stub import ...' in sub-tests

os.environ.setdefault("ENDLESS_LIBRARY_TEST", "1")
