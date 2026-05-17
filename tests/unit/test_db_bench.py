from __future__ import annotations

from pathlib import Path

import pytest

from endless_library.db.bench import BenchRunRepo
from endless_library.db.schema import init_db


@pytest.fixture
def repo(tmp_path: Path) -> BenchRunRepo:
    db = tmp_path / "library.db"
    init_db(db)
    return BenchRunRepo(db)


def test_record_and_success_rate(repo: BenchRunRepo) -> None:
    repo.record(scraper="annas_curl", query="A", success=True, duration_ms=300, http_code=200)
    repo.record(scraper="annas_curl", query="B", success=False, duration_ms=900, http_code=403)
    repo.record(scraper="annas_curl", query="C", success=True, duration_ms=400, http_code=200)
    rate = repo.success_rate(scraper="annas_curl", days=30)
    assert abs(rate - (2 / 3)) < 1e-6
