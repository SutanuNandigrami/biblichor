"""Tests for ultrareview I7 (ArchiveOrgCurl missing name)
and I8 (KindleBanglaCurl missing name).

Both scrapers omitted the class-level `name` attribute required by the
Scraper Protocol in base.py.  Accessing instance.name raised AttributeError
in any code path that introspects the scraper (logging, bench outcome records,
etc.).
"""

from __future__ import annotations


def test_archive_curl_has_name_attribute():
    """ArchiveOrgCurl must have name='archive_curl' (Scraper Protocol)."""
    from endless_library.scrapers.archive_curl import ArchiveOrgCurl

    assert hasattr(ArchiveOrgCurl, "name"), "ArchiveOrgCurl is missing class-level 'name'"
    assert ArchiveOrgCurl.name == "archive_curl", (
        f"expected 'archive_curl', got {ArchiveOrgCurl.name!r}"
    )


def test_kindlebangla_curl_has_name_attribute():
    """KindleBanglaCurl must have name='kindlebangla_curl' (Scraper Protocol)."""
    from endless_library.scrapers.kindlebangla_curl import KindleBanglaCurl

    assert hasattr(KindleBanglaCurl, "name"), "KindleBanglaCurl is missing class-level 'name'"
    assert KindleBanglaCurl.name == "kindlebangla_curl", (
        f"expected 'kindlebangla_curl', got {KindleBanglaCurl.name!r}"
    )


def test_all_registry_scrapers_have_name_attribute():
    """Every scraper in the registry must expose a 'name' class attribute."""
    from types import SimpleNamespace

    from endless_library.scrapers import registry

    # Build a minimal cfg stub so we can instantiate scrapers without real config
    SimpleNamespace(
        annas_mirrors=[],
        request_delay_seconds=0.0,
        slow_download_timeout_seconds=30,
        flaresolverr_url="http://localhost:8191/v1",
        tor_enabled=False,
        tor_proxy_url="",
        welib_auth_cookie=None,
        recent_release_window_years=1,
        mobilism_username="",
        mobilism_password="",
        bdebooks=SimpleNamespace(excluded_categories=set()),
        kindlebangla=SimpleNamespace(drive_folder_url="", service_account_json=""),
        format_priority=["epub"],
        language="en",
    )

    missing = []
    for name in registry.available():
        klass = registry._REGISTRY[name]
        if not hasattr(klass, "name"):
            missing.append(name)

    assert not missing, f"These scrapers are missing the class-level 'name' attribute: {missing}"


def test_archive_curl_name_matches_registry_key():
    """ArchiveOrgCurl.name must match its registry key 'archive_curl'."""
    from endless_library.scrapers.archive_curl import ArchiveOrgCurl
    from endless_library.scrapers.registry import _REGISTRY

    assert _REGISTRY["archive_curl"] is ArchiveOrgCurl
    assert ArchiveOrgCurl.name == "archive_curl"


def test_kindlebangla_curl_name_matches_registry_key():
    """KindleBanglaCurl.name must match its registry key 'kindlebangla_curl'."""
    from endless_library.scrapers.kindlebangla_curl import KindleBanglaCurl
    from endless_library.scrapers.registry import _REGISTRY

    assert _REGISTRY["kindlebangla_curl"] is KindleBanglaCurl
    assert KindleBanglaCurl.name == "kindlebangla_curl"
