"""Tests for Phase 6z Fix 5 (I-NEW-1): lxml XPath uses word-boundary class match.

contains(@class, "url") is a substring match and would match
class="sourceurl" or class="urlbar". The fix uses:
    contains(concat(' ', normalize-space(@class), ' '), ' url ')
which is a proper word-boundary check.
"""
from __future__ import annotations


def test_parse_domains_skips_substring_class_match():
    """Spans with class='sourceurl' must NOT contribute to domain list."""
    from endless_library.scrapers.annas_domains import parse_domains_from_html

    # Build HTML with an infobox vcard table containing:
    # - a span.sourceurl (should NOT match "url" word-boundary)
    # - a span.url (SHOULD match)
    html = b"""
    <html><body>
    <table class="infobox vcard">
      <tr>
        <td>
          <!-- Should NOT match (substring, not word) -->
          <span class="sourceurl"><a class="external" href="https://evil.com/should-not-appear">evil.com</a></span>
          <!-- SHOULD match -->
          <span class="url"><a class="external" href="https://annas-archive.gl/">annas-archive.gl</a></span>
        </td>
      </tr>
    </table>
    </body></html>
    """
    domains = parse_domains_from_html(html)
    assert "evil.com" not in domains, (
        f"evil.com from class='sourceurl' should NOT appear but got: {domains}"
    )
    assert "annas-archive.gl" in domains, (
        f"annas-archive.gl from class='url' SHOULD appear but got: {domains}"
    )


def test_parse_domains_skips_urlbar_class():
    """Spans with class='urlbar' must NOT match."""
    from endless_library.scrapers.annas_domains import parse_domains_from_html

    html = b"""
    <html><body>
    <table class="infobox vcard">
      <tr>
        <td>
          <span class="urlbar"><a class="external" href="https://bad.example.com/">bad.example.com</a></span>
          <span class="url"><a class="external" href="https://annas-archive.li/">annas-archive.li</a></span>
        </td>
      </tr>
    </table>
    </body></html>
    """
    domains = parse_domains_from_html(html)
    assert "bad.example.com" not in domains
    assert "annas-archive.li" in domains


def test_parse_domains_matches_multi_class():
    """span with class='external url' (url as one word in a multi-class) should match."""
    from endless_library.scrapers.annas_domains import parse_domains_from_html

    html = b"""
    <html><body>
    <table class="infobox vcard">
      <tr>
        <td>
          <span class="external url"><a class="external" href="https://annas-archive.pm/">annas-archive.pm</a></span>
        </td>
      </tr>
    </table>
    </body></html>
    """
    domains = parse_domains_from_html(html)
    assert "annas-archive.pm" in domains


def test_parse_domains_real_infobox_not_affected_by_fix():
    """Existing real infobox parsing still finds valid domains after fix."""
    from endless_library.scrapers.annas_domains import parse_domains_from_html

    html = b"""
    <html><body>
    <table class="infobox biography vcard">
      <tr><th>Website</th></tr>
      <tr>
        <td>
          <span class="url"><a class="external text" href="//annas-archive.in/">annas-archive.in</a></span>
        </td>
      </tr>
    </table>
    </body></html>
    """
    domains = parse_domains_from_html(html)
    assert "annas-archive.in" in domains
