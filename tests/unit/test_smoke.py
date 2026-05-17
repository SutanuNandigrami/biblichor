from __future__ import annotations

import endless_library


def test_package_version() -> None:
    assert endless_library.__version__ == "0.1.0"
