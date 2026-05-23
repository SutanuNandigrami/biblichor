"""annas_cloakbrowser — Phase 6w.2 rewrite.

Now talks to the `cf-bypass` sidecar (sarperavci/CloudflareBypassForScraping).
Sidecar runs DrissionPage + patched Chromium inside the biblichor compose
network and resolves Cloudflare interactive challenges. Same registry
slot + name as before (so cfg.scrapers.order doesn't churn), but the
internals are replaced.

provider stays "annas" to satisfy the Candidate.provider Literal constraint.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.scrapers import annas_domains, cf_bypass_client

log = logging.getLogger(__name__)


class AnnasArchiveCloakBrowser:
    name = "annas_cloakbrowser"
    provider = "annas"

    def __init__(self, cfg, **kw):
        self._cfg = cfg

    def search(self, query: SearchQuery) -> list[Candidate]:
        host = annas_domains.next_mirror()
        q = urlencode({"q": query.title, "ext": "epub", "sort": ""})
        url = f"https://{host}/search?{q}"
        try:
            html = cf_bypass_client.resolve(url)
            annas_domains.mark_success(host)
        except Exception as e:
            log.warning("cf-bypass resolve failed for %s: %s", url, e)
            annas_domains.mark_cool(host)
            return []
        return _parse_search_results(html, host)

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        try:
            html = cf_bypass_client.resolve(candidate.detail_url)
        except Exception as e:
            log.warning("cf-bypass resolve failed for %s: %s", candidate.detail_url, e)
            return None
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href*='/d3/y/']"):
            return DownloadHandle(url=a["href"], headers={})
        return None


def _parse_search_results(html: str, host: str) -> list[Candidate]:
    soup = BeautifulSoup(html, "lxml")
    out: list[Candidate] = []
    for a in soup.select("a[href^='/md5/']"):
        md5_url = f"https://{host}{a['href']}"
        title = a.get_text(" ", strip=True)[:200]
        out.append(Candidate(
            title=title,
            provider="annas",
            md5=None,
            author=None,
            language=None,
            format="epub",
            filesize_bytes=None,
            year=None,
            publisher=None,
            edition_hints="",
            detail_url=md5_url,
        ))
    return out
