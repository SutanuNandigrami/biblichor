"""Tests for the Wikipedia-driven Anna's Archive mirror discovery."""

from __future__ import annotations

import time
from pathlib import Path

from endless_library.scrapers.annas_domains import (
    DEFAULT_UPDATE_INTERVAL_SECONDS,
    cache_path,
    effective_mirrors,
    fetch_wiki_domains,
    is_stale,
    parse_domains_from_html,
    read_cache,
    update_cache_if_stale,
    write_cache,
)

# A minimal slice of what the real Wikipedia page looks like — only the bits
# our parser cares about (vcard infobox + url spans + external-text anchors).
WIKI_FIXTURE_HTML = b"""
<html><body>
<table class="infobox vcard" cellspacing="0">
  <tr><th>Type</th><td>Shadow library</td></tr>
  <tr><th>URL</th><td>
    <span class="url"><a class="external text" href="https://annas-archive.org/">annas-archive.org</a></span>
    <span class="url"><a class="external text" href="https://annas-archive.gl">annas-archive.gl</a></span>
    <span class="url"><a class="external text" href="//annas-archive.pk">annas-archive.pk</a></span>
    <span class="url"><a class="external text" href="https://annas-archive.gd/">annas-archive.gd</a></span>
    <span class="url"><a class="external text" href="https://annas-archive.se">annas-archive.se</a></span>
  </td></tr>
</table>
<h2>Article body</h2>
<table>
  <tr><td><span class="url"><a class="external text" href="https://unrelated.example">noise</a></span></td></tr>
</table>
</body></html>
"""


def test_parse_extracts_domains_from_infobox_only():
    domains = parse_domains_from_html(WIKI_FIXTURE_HTML)
    # We pick up the 5 vcard entries, NOT the unrelated link below the <h2>.
    assert domains == [
        "annas-archive.org",
        "annas-archive.gl",
        "annas-archive.pk",
        "annas-archive.gd",
        "annas-archive.se",
    ]


def test_parse_returns_empty_when_infobox_missing():
    assert parse_domains_from_html(b"<html><body>nothing here</body></html>") == []


def test_parse_handles_str_input():
    assert parse_domains_from_html(WIKI_FIXTURE_HTML.decode("utf-8")) == [
        "annas-archive.org",
        "annas-archive.gl",
        "annas-archive.pk",
        "annas-archive.gd",
        "annas-archive.se",
    ]


def test_fetch_returns_empty_on_error_status():
    def fake_get(_url: str) -> tuple[int, bytes]:
        return 503, b"upstream down"

    assert fetch_wiki_domains(http_get=fake_get) == []


def test_fetch_returns_empty_on_exception():
    def fake_get(_url: str) -> tuple[int, bytes]:
        raise RuntimeError("network gone")

    assert fetch_wiki_domains(http_get=fake_get) == []


def test_fetch_parses_on_200():
    def fake_get(_url: str) -> tuple[int, bytes]:
        return 200, WIKI_FIXTURE_HTML

    assert fetch_wiki_domains(http_get=fake_get)[0] == "annas-archive.org"


def test_cache_roundtrip(tmp_path: Path):
    p = cache_path(tmp_path)
    domains = ["annas-archive.gl", "annas-archive.se"]
    write_cache(p, domains)
    got, ts = read_cache(p)
    assert got == domains
    assert ts > 0


def test_read_cache_missing_returns_empty(tmp_path: Path):
    domains, ts = read_cache(tmp_path / "absent.json")
    assert domains == [] and ts == 0


def test_read_cache_corrupt_returns_empty(tmp_path: Path):
    p = tmp_path / "wiki.json"
    p.write_text("not json")
    assert read_cache(p) == ([], 0)


def test_is_stale_when_old():
    assert is_stale(time.time() - DEFAULT_UPDATE_INTERVAL_SECONDS - 1)


def test_is_stale_when_fresh():
    assert not is_stale(time.time() - 60)


def test_is_stale_zero_means_never_cached():
    assert is_stale(0)


def test_update_skips_fetch_when_fresh(tmp_path: Path):
    p = cache_path(tmp_path)
    write_cache(p, ["annas-archive.gl"])
    called: list[str] = []

    def fake_get(url: str) -> tuple[int, bytes]:
        called.append(url)
        return 200, WIKI_FIXTURE_HTML

    got = update_cache_if_stale(p, http_get=fake_get)
    # Cache is fresh → no fetch, original list returned
    assert called == []
    assert got == ["annas-archive.gl"]


def test_update_fetches_when_stale(tmp_path: Path):
    p = cache_path(tmp_path)

    def fake_get(_url: str) -> tuple[int, bytes]:
        return 200, WIKI_FIXTURE_HTML

    got = update_cache_if_stale(p, http_get=fake_get)
    assert "annas-archive.gl" in got
    # Cache file got written
    cached, ts = read_cache(p)
    assert cached == got
    assert ts > time.time() - 5


def test_update_keeps_stale_on_fetch_failure(tmp_path: Path):
    """When the fetch fails, do NOT discard the old cache — drift over time
    is better than losing every known mirror in one bad Wikipedia request."""
    p = cache_path(tmp_path)
    # Force a stale timestamp by writing one then rewinding it
    import json

    p.write_text(
        json.dumps(
            {
                "domains": ["annas-archive.gl"],
                "timestamp": time.time() - DEFAULT_UPDATE_INTERVAL_SECONDS - 60,
            }
        )
    )

    def fake_get(_url: str) -> tuple[int, bytes]:
        return 503, b""

    got = update_cache_if_stale(p, http_get=fake_get)
    assert got == ["annas-archive.gl"]


def test_effective_mirrors_dedupes_and_normalizes():
    configured = ["https://annas-archive.gl", "annas-archive.pk"]
    cached = ["annas-archive.gl", "annas-archive.se", "https://annas-archive.gd/"]
    out = effective_mirrors(configured, cached)
    assert out == [
        "https://annas-archive.gl",
        "https://annas-archive.pk",
        "https://annas-archive.se",
        "https://annas-archive.gd",
    ]


def test_effective_mirrors_preserves_configured_order():
    configured = ["annas-archive.pk", "annas-archive.gl", "annas-archive.gd"]
    cached = ["annas-archive.gl", "annas-archive.pk"]
    out = effective_mirrors(configured, cached)
    assert out == [
        "https://annas-archive.pk",
        "https://annas-archive.gl",
        "https://annas-archive.gd",
    ]


def test_effective_mirrors_ignores_empty_entries():
    assert effective_mirrors(["", "annas-archive.gl"], ["", "annas-archive.se"]) == [
        "https://annas-archive.gl",
        "https://annas-archive.se",
    ]


# ---------------------------------------------------------------------------
# Ultrareview B: thread-safe annas_domains state
# ---------------------------------------------------------------------------

def test_annas_domains_thread_safe():
    """20 threads mix of mark_cool/mark_success/next_mirror — no exception,
    state stays consistent (at most one _last_working value)."""
    import threading
    from endless_library.scrapers import annas_domains as ad

    ad._reset_state_for_tests()

    errors = []
    mirrors = list(ad._MIRRORS)

    def worker(i):
        try:
            mirror = mirrors[i % len(mirrors)]
            if i % 3 == 0:
                ad.mark_cool(mirror)
            elif i % 3 == 1:
                ad.mark_success(mirror)
            else:
                ad.next_mirror()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], "Thread errors: " + str(errors)
    # _last_working must be None or a single valid mirror (never a torn value)
    with ad._STATE_LOCK:
        lw = ad._last_working
    assert lw is None or lw in mirrors
    # _state values must all be floats (no torn writes)
    with ad._STATE_LOCK:
        for v in ad._state.values():
            assert isinstance(v, float), f"Non-float cool-until: {v!r}"
    ad._reset_state_for_tests()
