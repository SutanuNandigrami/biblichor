# tests/unit/test_phase6w2_annas_hardening.py
import time


def test_next_mirror_returns_first_when_no_history():
    from endless_library.scrapers.annas_domains import next_mirror, _reset_state
    _reset_state()
    m = next_mirror()
    assert m in {"annas-archive.gl", "annas-archive.li", "annas-archive.pm", "annas-archive.in"}


def test_mark_cool_skips_mirror_for_5min():
    from endless_library.scrapers.annas_domains import (
        next_mirror, mark_cool, _reset_state, _MIRRORS,
    )
    _reset_state()
    cooled = _MIRRORS[0]
    mark_cool(cooled)
    seen = set()
    for _ in range(10):
        seen.add(next_mirror())
    assert cooled not in seen


def test_mark_success_prefers_last_working():
    from endless_library.scrapers.annas_domains import (
        next_mirror, mark_success, _reset_state, _MIRRORS,
    )
    _reset_state()
    pick = _MIRRORS[2]
    mark_success(pick)
    assert next_mirror(prefer_last_working=True) == pick


def test_cool_expires_after_300_seconds(monkeypatch):
    import endless_library.scrapers.annas_domains as ad
    ad._reset_state()
    ad.mark_cool(ad._MIRRORS[0])
    monkeypatch.setattr(ad, "_now", lambda: time.time() + 301)
    seen = set()
    for _ in range(10):
        seen.add(ad.next_mirror())
    assert ad._MIRRORS[0] in seen
