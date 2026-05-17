from __future__ import annotations

from endless_library.scrapers.rate_limit import MirrorRotator, TokenBucket


def test_token_bucket_lets_through_under_capacity():
    b = TokenBucket(capacity=3, period_seconds=60)
    t = 1000.0
    assert b.acquire("http://x/a", now=t) == 0.0
    assert b.acquire("http://x/b", now=t + 1) == 0.0
    assert b.acquire("http://x/c", now=t + 2) == 0.0


def test_token_bucket_throttles_at_capacity():
    b = TokenBucket(capacity=2, period_seconds=60)
    t = 1000.0
    b.acquire("http://x/", now=t)
    b.acquire("http://x/", now=t)
    wait = b.acquire("http://x/", now=t + 5)
    assert wait > 0  # must wait until oldest ages out


def test_token_bucket_per_host_isolated():
    b = TokenBucket(capacity=1, period_seconds=60)
    t = 1000.0
    assert b.acquire("http://x/", now=t) == 0.0
    assert b.acquire("http://y/", now=t) == 0.0  # different host


def test_mirror_rotator():
    r = MirrorRotator(["https://a/", "https://b/", "https://c/"])
    assert r.current == "https://a"
    assert r.next_after_failure() == "https://b"
    assert r.next_after_failure() == "https://c"
    assert r.next_after_failure() == "https://a"  # wrap-around


def test_mirror_rotator_requires_one():
    import pytest

    with pytest.raises(ValueError):
        MirrorRotator([])
