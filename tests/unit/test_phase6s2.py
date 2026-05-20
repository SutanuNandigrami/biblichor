"""Phase 6s.2 tests — IPFS gateway refresh + parallel slow-server +
LibGen ladder + Wayback CDX fallback."""

from __future__ import annotations

import re

import httpx
import respx

# ============ Task 8: ipfs_gateways table + module ============


def test_ipfs_gateways_table_exists(tmp_path):
    from endless_library.db.schema import connect, init_db

    db = tmp_path / "library.db"
    init_db(db)
    with connect(db) as conn:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert "ipfs_gateways" in tables


@respx.mock(assert_all_called=False)
def test_ipfs_gateways_refresh_populates_table(respx_mock, tmp_path):
    respx_mock.get(
        "https://raw.githubusercontent.com/ipfs/public-gateway-checker/main/gateways.json"
    ).mock(
        return_value=httpx.Response(
            200,
            json=[
                "https://ipfs.io",
                "https://dweb.link",
                "https://cf-ipfs.com",
            ],
        )
    )

    from endless_library.db.schema import connect, init_db
    from endless_library.ipfs_gateways import refresh_gateway_list

    db = tmp_path / "library.db"
    init_db(db)
    n = refresh_gateway_list(db_path=db)
    assert n == 3

    with connect(db) as conn:
        rows = conn.execute("SELECT url FROM ipfs_gateways").fetchall()
    urls = {r[0] for r in rows}
    assert "https://ipfs.io" in urls


def test_ipfs_gateways_fallback_to_bootstrap_when_table_empty(tmp_path):
    """No refresh has run; list_gateways returns the bootstrap baseline."""
    from endless_library.db.schema import init_db
    from endless_library.ipfs_gateways import list_gateways

    db = tmp_path / "library.db"
    init_db(db)
    urls = list_gateways(db_path=db)
    assert len(urls) >= 5
    assert all(u.startswith("http") for u in urls)


def test_ipfs_gateways_list_with_none_db_returns_bootstrap():
    from endless_library.ipfs_gateways import list_gateways

    urls = list_gateways(db_path=None)
    assert len(urls) >= 5


# ============ Task 10: parallel slow-server probes ============


def test_annas_curl_has_async_probe_helper():
    """The parallel probe helper is exposed for testing the latency
    win. Implementation lives in annas_curl module."""
    from endless_library.scrapers import annas_curl

    assert hasattr(annas_curl, "_probe_slow_servers_async") or hasattr(
        annas_curl, "_race_slow_servers"
    ), "Phase 6s.2 must expose a parallel-probe helper"


# ============ Task 11: LibGen mirror ladder ============


def test_libgen_mirror_ladder_li_is_first():
    """Most-stable May 2026 is .li; it must be the primary attempt."""
    from endless_library.scrapers.libgen_curl import LIBGEN_MIRRORS

    assert "libgen.li" in LIBGEN_MIRRORS[0]


def test_libgen_mirror_ladder_has_2026_promoted_mirrors():
    from endless_library.scrapers.libgen_curl import LIBGEN_MIRRORS

    joined = " ".join(LIBGEN_MIRRORS)
    for current in ("libgen.la", "libgen.gl", "libgen.bz", "libgen.vg"):
        assert current in joined, f"{current} should be a primary 2026 mirror"


def test_libgen_mirror_ladder_does_not_include_seized_st():
    from endless_library.scrapers.libgen_curl import LIBGEN_MIRRORS

    joined = " ".join(LIBGEN_MIRRORS)
    assert "libgen.st" not in joined, "libgen.st was seized 2024-12; remove"


# ============ Task 12: Wayback CDX fallback ============


@respx.mock(assert_all_called=False)
def test_wayback_recover_links_extracts_ipfs_cids(respx_mock):
    """When called with a md5, query Wayback CDX for the snapshot
    history, fetch the most recent archived page, extract IPFS CIDs."""
    respx_mock.get("https://web.archive.org/cdx/search/cdx").mock(
        return_value=httpx.Response(
            200,
            json=[
                [
                    "urlkey",
                    "timestamp",
                    "original",
                    "mimetype",
                    "statuscode",
                    "digest",
                    "length",
                ],
                [
                    "org,annas-archive)/md5/abc",
                    "20240601120000",
                    "https://annas-archive.org/md5/abc",
                    "text/html",
                    "200",
                    "x",
                    "1",
                ],
            ],
        )
    )
    respx_mock.get(re.compile(r"https://web\.archive\.org/web/.*/.*")).mock(
        return_value=httpx.Response(
            200,
            text="""
              <html>
                <body>
                  <a href="ipfs://QmFOOBAR0000000000000000000000000000000000000">Download via IPFS</a>
                </body>
              </html>
            """,
        )
    )

    from endless_library.scrapers.wayback_fallback import recover_links

    handles = recover_links("abc")
    assert len(handles) >= 1
    assert any("QmFOOBAR" in h.url for h in handles)


def test_wayback_recover_links_with_no_md5_returns_empty():
    from endless_library.scrapers.wayback_fallback import recover_links

    assert recover_links("") == []
