from __future__ import annotations

from pathlib import Path

import pytest

from endless_library.db.mirrors import MirrorRepo
from endless_library.db.schema import init_db


@pytest.fixture
def repo(tmp_path: Path) -> MirrorRepo:
    db = tmp_path / "library.db"
    init_db(db)
    return MirrorRepo(db)


def test_seed_inserts_curated(repo: MirrorRepo) -> None:
    n = repo.seed_curated()
    assert n >= 3  # at least the 3 annas mirrors
    urls = {m.url for m in repo.list_all()}
    assert "https://annas-archive.gl" in urls
    assert "https://annas-archive.pk" in urls
    assert "https://annas-archive.gd" in urls


def test_seed_is_idempotent(repo: MirrorRepo) -> None:
    a = repo.seed_curated()
    b = repo.seed_curated()
    assert a > 0
    assert b == 0


def test_record_probe_success(repo: MirrorRepo) -> None:
    repo.seed_curated()
    mid = repo.list_all()[0].id
    repo.record_probe(mid, ok=True, status=200, latency_ms=123, error=None)
    row = repo.get(mid)
    assert row.last_ok_at is not None
    assert row.last_status == 200
    assert row.consecutive_failures == 0


def test_auto_disable_after_failures(repo: MirrorRepo) -> None:
    repo.seed_curated()
    mid = repo.list_all()[0].id
    for _ in range(MirrorRepo.AUTO_DISABLE_AFTER):
        repo.record_probe(mid, ok=False, status=None, latency_ms=999, error="timeout")
    row = repo.get(mid)
    assert row.enabled is False
    assert row.consecutive_failures >= MirrorRepo.AUTO_DISABLE_AFTER


def test_healthy_urls_filters_disabled(repo: MirrorRepo) -> None:
    repo.seed_curated()
    annas = repo.list_all(kind="annas")
    assert annas
    repo.set_enabled(annas[0].id, False)
    healthy = repo.healthy_urls(kind="annas")
    assert annas[0].url not in healthy


def test_add_user_mirror(repo: MirrorRepo) -> None:
    mid = repo.add(kind="annas", url="https://custom-annas.example", label="my mirror")
    row = repo.get(mid)
    assert row is not None
    assert row.url == "https://custom-annas.example"
