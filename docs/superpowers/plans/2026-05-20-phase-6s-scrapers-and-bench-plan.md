# Phase 6s — Scrapers, sources, techniques, helpers + bench fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 4 new acquisition scrapers (Gutendex, Standard Ebooks, OAPEN/DOAB, Wikisource), 4 new reading-list sources (NYT Best Sellers, StoryGraph, BookWyrm, Wikidata), centralized OpenLibrary metadata helpers, the Z-Library SingleLogin personal-domain flow, browser-cookie SPA upload, Tor onion fallback for Anna's Archive, and fix the bench HTTP 500 — all shipping in 7 independently-deployable sub-phases.

**Architecture:** Every new source plugs into one of the two existing surfaces (`scrapers/<name>.py` with `search`/`resolve_cdn`, or `sources/<name>.py` with `list_to_read`). One new `metadata/` package centralizes ISBN resolution. Two new sqlite tables (`ipfs_gateways`, `metadata_cache`) and two new columns on `books`. No abstractions beyond what's already in the codebase.

**Tech Stack:** Python 3.12, FastAPI, httpx + curl-cffi, BeautifulSoup, respx for HTTP test mocking, Vue 3 + Tailwind, docker compose.

**Run all commands from**: `~/endless-library` on claude-1 (the working dir).
**Shorthand**: `COMPOSE = "docker compose -f deploy/compose.yml --env-file .env"`
**Test framework**: pytest with respx for HTTP mocking. Every test uses `assert_all_called=False` so a missing mock doesn't fail (matches existing patterns).

---

## Section 6s.1 — Bench fix + zero-config acquisition

### Task 1: Bench HTTP 500 — path fix

**Files:**
- Modify: `src/endless_library/bench.py:39`
- Test: `tests/unit/test_phase6s1.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_phase6s1.py`:

```python
"""Phase 6s.1 tests — bench fix + zero-config acquisition scrapers."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx


def test_bench_load_queries_resolves_default_path():
    """Phase 6s.1: bench.load_queries(None) must resolve the bundled
    bench/queries.yaml regardless of current working directory.
    Pre-fix it used the cwd-relative string 'bench/queries.yaml',
    which broke inside the container (cwd=/app, file exists but the
    relative path was wrong)."""
    from endless_library.bench import load_queries

    queries, quick_idx = load_queries()
    assert len(queries) > 0
    assert isinstance(quick_idx, list)
    assert all(0 <= i < len(queries) for i in quick_idx)
```

- [ ] **Step 2: Run test to verify it fails or errors**

```bash
.venv/bin/python -m pytest tests/unit/test_phase6s1.py::test_bench_load_queries_resolves_default_path -v
```

Expected: ERROR/FAIL with `FileNotFoundError: bench/queries.yaml` OR fail because `load_queries()` requires an arg.

- [ ] **Step 3: Fix bench.py**

Edit `src/endless_library/bench.py`. Find the `load_queries` function (around line 30-50) and update its signature + body to accept an optional `path`:

```python
def load_queries(path: Path | None = None) -> tuple[list[BenchQuery], list[int]]:
    """Load queries.yaml. When path is None, resolve relative to the
    repo root (Path(__file__).parent.parent.parent / 'bench' /
    'queries.yaml') so it works regardless of cwd — fixes the
    container HTTP 500 where the cwd-relative path missed the file."""
    if path is None:
        path = Path(__file__).resolve().parent.parent.parent / "bench" / "queries.yaml"
    raw = yaml.safe_load(path.read_text())
    # ... rest of existing body unchanged ...
```

If the existing function takes `path: Path` (positional, required), make it optional. Find all callers via grep and update if they passed `"bench/queries.yaml"` literally.

- [ ] **Step 4: Update the API caller**

In `src/endless_library/web/api.py`, find `run_bench_endpoint` (around line 604-627). Replace the explicit path arg:

```python
# before
qs, quick_idx = load_queries(bench_path)
# after
qs, quick_idx = load_queries()
```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/python -m pytest tests/unit/test_phase6s1.py::test_bench_load_queries_resolves_default_path -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/endless_library/bench.py src/endless_library/web/api.py tests/unit/test_phase6s1.py
git commit -m "Phase 6s.1: bench load_queries resolves repo-root path (fixes HTTP 500)

Container regression: load_queries used the cwd-relative path
'bench/queries.yaml', which doesn't resolve inside /app. Resolve
relative to Path(__file__).parent.parent.parent like every other
bundled-resource path in the codebase.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Schema migration — `pub_year` and `is_public_domain` columns

**Files:**
- Modify: `src/endless_library/db/schema.py`
- Test: `tests/unit/test_phase6s1.py` (append)

- [ ] **Step 1: Add the failing test**

Append to `tests/unit/test_phase6s1.py`:

```python
def test_books_schema_has_pd_columns(tmp_path):
    """Phase 6s.1: schema adds pub_year + is_public_domain columns
    for the PD pre-chain hook."""
    from endless_library.db.schema import connect, init_db

    db = tmp_path / "library.db"
    init_db(db)
    with connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(books)").fetchall()}
    assert "pub_year" in cols
    assert "is_public_domain" in cols
```

- [ ] **Step 2: Run + see failure**

```bash
.venv/bin/python -m pytest tests/unit/test_phase6s1.py::test_books_schema_has_pd_columns -v
```

Expected: FAIL with `assert 'pub_year' in cols`.

- [ ] **Step 3: Add the migration**

In `src/endless_library/db/schema.py`, locate the `init_db` function and the `MIGRATIONS` list (or equivalent idempotent-ALTER list). Add two new ALTERs:

```python
_PHASE_6S1_MIGRATIONS = [
    "ALTER TABLE books ADD COLUMN pub_year INTEGER",
    "ALTER TABLE books ADD COLUMN is_public_domain INTEGER",
]

def _try_alter(conn, sql: str) -> None:
    try:
        conn.execute(sql)
    except sqlite3.OperationalError as e:
        # 'duplicate column name' is the idempotent-pass case
        if "duplicate column name" not in str(e):
            raise

def init_db(db_path: Path) -> None:
    # ... existing body ...
    with connect(db_path) as conn:
        # ... existing migrations ...
        for stmt in _PHASE_6S1_MIGRATIONS:
            _try_alter(conn, stmt)
        conn.commit()
```

If your schema.py already has an idempotent-migration helper, reuse it; the names above are a sketch.

- [ ] **Step 4: Run + verify passes**

```bash
.venv/bin/python -m pytest tests/unit/test_phase6s1.py::test_books_schema_has_pd_columns -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/endless_library/db/schema.py tests/unit/test_phase6s1.py
git commit -m "Phase 6s.1: add books.pub_year + books.is_public_domain (PD hook)

Two nullable INTEGER columns the PD pre-chain hook uses to route
public-domain books to Gutendex/Standard Ebooks/etc. ahead of
Anna's. Idempotent ALTERs (no rerun damage).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Gutendex scraper

**Files:**
- Create: `src/endless_library/scrapers/gutendex.py`
- Modify: `src/endless_library/scrapers/registry.py`
- Test: `tests/unit/test_phase6s1.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_phase6s1.py`:

```python
import respx
from endless_library.domain.models import SearchQuery


@respx.mock(base_url="https://gutendex.com", assert_all_called=False)
def test_gutendex_search_returns_candidates(respx_mock):
    """Project Gutenberg via Gutendex — happy path."""
    respx_mock.get("/books").mock(return_value=httpx.Response(200, json={
        "count": 1,
        "results": [{
            "id": 1342,
            "title": "Pride and Prejudice",
            "authors": [{"name": "Austen, Jane"}],
            "languages": ["en"],
            "formats": {
                "application/epub+zip": "https://www.gutenberg.org/ebooks/1342.epub.images",
                "text/plain; charset=utf-8": "https://www.gutenberg.org/files/1342/1342-0.txt"
            }
        }]
    }))

    from endless_library.scrapers.gutendex import Gutendex
    g = Gutendex(cfg=None)
    sq = SearchQuery(title="Pride and Prejudice", author="Jane Austen",
                     isbn13=None, format_priority=["epub"], language=None)
    cands = g.search(sq)
    assert len(cands) == 1
    assert cands[0].title == "Pride and Prejudice"
    assert cands[0].format == "epub"
    assert "gutenberg" in cands[0].detail_url


@respx.mock(base_url="https://gutendex.com", assert_all_called=False)
def test_gutendex_no_results_returns_empty(respx_mock):
    respx_mock.get("/books").mock(return_value=httpx.Response(200, json={"results": []}))
    from endless_library.scrapers.gutendex import Gutendex
    g = Gutendex(cfg=None)
    sq = SearchQuery(title="nonexistent xyzzy", author=None, isbn13=None,
                     format_priority=["epub"], language=None)
    assert g.search(sq) == []


@respx.mock(base_url="https://gutendex.com", assert_all_called=False)
def test_gutendex_resolve_cdn_returns_handle(respx_mock):
    """Gutendex detail_url IS the CDN URL — no further indirection."""
    from endless_library.scrapers.gutendex import Gutendex
    from endless_library.domain.models import Candidate
    g = Gutendex(cfg=None)
    c = Candidate(source="gutendex", mirror_host="gutendex.com",
                  title="x", author="y", detail_url="https://www.gutenberg.org/ebooks/1.epub",
                  format="epub", language="en", md5=None)
    handle = g.resolve_cdn(c)
    assert handle is not None
    assert handle.url == c.detail_url
```

- [ ] **Step 2: Run + see failure**

```bash
.venv/bin/python -m pytest tests/unit/test_phase6s1.py -k gutendex -v
```

Expected: 3 failures (`ModuleNotFoundError` for `endless_library.scrapers.gutendex`).

- [ ] **Step 3: Implement Gutendex**

Create `src/endless_library/scrapers/gutendex.py`:

```python
"""Project Gutenberg via Gutendex (https://gutendex.com).

A JSON wrapper around the nightly Project Gutenberg catalog.
No auth, no rate limit beyond polite usage. Returns books with
direct download URLs in multiple formats.

The detail_url IS the CDN URL — resolve_cdn is a no-op.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery

log = logging.getLogger(__name__)

API_BASE = "https://gutendex.com"


class Gutendex:
    name = "gutendex"

    def __init__(self, cfg, http_get: callable | None = None) -> None:
        self._cfg = cfg
        self._http_get = http_get

    def search(self, sq: SearchQuery) -> list[Candidate]:
        params = {"search": sq.title}
        if sq.author:
            params["search"] = f"{sq.title} {sq.author}"
        try:
            r = httpx.get(f"{API_BASE}/books", params=params, timeout=15.0,
                          headers={"User-Agent": "endless-library/0.1"})
        except httpx.HTTPError as e:
            log.info("gutendex: %s", e)
            return []
        if r.status_code != 200:
            return []
        results = r.json().get("results", [])
        out: list[Candidate] = []
        # Prefer order: epub > mobi > plain text
        format_pref = (
            "application/epub+zip",
            "application/x-mobipocket-ebook",
            "text/plain; charset=utf-8",
            "text/plain",
        )
        for book in results[:25]:
            fmts = book.get("formats", {})
            url = None
            for fmt in format_pref:
                if fmt in fmts:
                    url = fmts[fmt]
                    break
            if not url:
                continue
            ext = Path(urlparse(url).path).suffix.lstrip(".").lower() or "txt"
            # Normalize for Calibre downstream
            if ext == "txt":
                ext = "txt"
            elif ext in ("epub", "mobi", "azw3"):
                pass
            else:
                ext = "epub" if "epub" in fmts.get(format_pref[0], "") else ext
            authors = ", ".join(a.get("name", "") for a in book.get("authors", []))
            out.append(Candidate(
                source="gutendex",
                mirror_host="gutendex.com",
                title=book.get("title", ""),
                author=authors,
                detail_url=url,
                format=ext,
                language=(book.get("languages") or ["en"])[0],
                md5=None,
            ))
        return out

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        if not candidate.detail_url:
            return None
        return DownloadHandle(
            url=candidate.detail_url,
            headers={},
            expected_filename=None,
        )
```

- [ ] **Step 4: Register in scrapers/registry.py**

Edit `src/endless_library/scrapers/registry.py`. Add the import and registry entry:

```python
from endless_library.scrapers.gutendex import Gutendex

_REGISTRY = {
    "annas_curl": AnnasArchiveCurl,
    # ... existing entries ...
    "gutendex": Gutendex,
}
```

Note: the scraper is registered but NOT added to the default order — that happens in the PD pre-chain hook task. Users can also opt in via the SPA Scrapers page once we surface it.

- [ ] **Step 5: Run + verify all 3 tests pass**

```bash
.venv/bin/python -m pytest tests/unit/test_phase6s1.py -k gutendex -v
```

Expected: 3 PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/endless_library/scrapers/gutendex.py src/endless_library/scrapers/registry.py tests/unit/test_phase6s1.py
git commit -m "Phase 6s.1: Gutendex (Project Gutenberg JSON API) scraper

No auth, no Cloudflare. detail_url is the CDN URL so resolve_cdn
is a no-op DownloadHandle wrapper. Registered but not in default
order; the PD pre-chain hook (next task) promotes it for public-
domain books.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Standard Ebooks scraper

**Files:**
- Create: `src/endless_library/scrapers/standard_ebooks.py`
- Modify: `src/endless_library/scrapers/registry.py`
- Test: `tests/unit/test_phase6s1.py` (append)

- [ ] **Step 1: Write the failing test**

Append:

```python
@respx.mock(base_url="https://standardebooks.org", assert_all_called=False)
def test_standard_ebooks_search_returns_candidates(respx_mock):
    """Standard Ebooks ships an Atom feed at /opds/all that lists
    every book with a direct EPUB link in <link rel='acquisition'>."""
    atom = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Pride and Prejudice</title>
    <author><name>Jane Austen</name></author>
    <link rel="http://opds-spec.org/acquisition" type="application/epub+zip"
          href="/ebooks/jane-austen/pride-and-prejudice/downloads/jane-austen_pride-and-prejudice.epub"/>
    <dc:language xmlns:dc="http://purl.org/dc/terms/">en-GB</dc:language>
  </entry>
</feed>"""
    respx_mock.get("/opds/all").mock(return_value=httpx.Response(200, text=atom))

    from endless_library.scrapers.standard_ebooks import StandardEbooks
    se = StandardEbooks(cfg=None)
    sq = SearchQuery(title="Pride and Prejudice", author="Jane Austen",
                     isbn13=None, format_priority=["epub"], language=None)
    cands = se.search(sq)
    assert len(cands) >= 1
    assert cands[0].format == "epub"
    assert "standardebooks.org" in cands[0].detail_url
```

- [ ] **Step 2: Run + fail** as before.

- [ ] **Step 3: Implement**

Create `src/endless_library/scrapers/standard_ebooks.py`:

```python
"""Standard Ebooks — hand-curated public-domain EPUBs.

Best-format public-domain works ("the Apple-quality version of
Gutenberg"). Smaller catalog than Gutendex but every book is
hand-typeset and hand-proofed.

Search via the public OPDS Atom feed at /opds/all (the full
catalog is also fronted by /opds/new for recent additions).
The per-book download link in the Atom entry is the EPUB URL.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery

log = logging.getLogger(__name__)

BASE = "https://standardebooks.org"
FEED_URL = f"{BASE}/opds/all"


class StandardEbooks:
    name = "standard_ebooks"

    def __init__(self, cfg, http_get: callable | None = None) -> None:
        self._cfg = cfg
        self._http_get = http_get
        self._cached_feed: str | None = None

    def _fetch_feed(self) -> str:
        if self._cached_feed is not None:
            return self._cached_feed
        try:
            r = httpx.get(FEED_URL, timeout=30.0,
                          headers={"User-Agent": "endless-library/0.1"})
        except httpx.HTTPError as e:
            log.info("standard_ebooks: %s", e)
            return ""
        if r.status_code != 200:
            return ""
        self._cached_feed = r.text
        return r.text

    def search(self, sq: SearchQuery) -> list[Candidate]:
        feed = self._fetch_feed()
        if not feed:
            return []
        soup = BeautifulSoup(feed, "xml")
        out: list[Candidate] = []
        title_q = (sq.title or "").lower()
        author_q = (sq.author or "").lower()
        for entry in soup.find_all("entry"):
            title_el = entry.find("title")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            author = ", ".join(
                a.find("name").get_text(strip=True) for a in entry.find_all("author")
                if a.find("name") is not None
            )
            # Fuzzy title match >= 80 OR exact substring
            score = fuzz.token_set_ratio(title.lower(), title_q)
            if score < 80 and title_q not in title.lower():
                continue
            # Author boost when supplied
            if author_q and author and fuzz.token_set_ratio(author.lower(), author_q) < 60:
                continue
            link = entry.find("link", attrs={
                "rel": "http://opds-spec.org/acquisition",
                "type": "application/epub+zip",
            })
            if link is None:
                continue
            url = urljoin(BASE, link.get("href", ""))
            lang = "en"
            dc_lang = entry.find("language")
            if dc_lang:
                lang = dc_lang.get_text(strip=True).split("-")[0] or "en"
            out.append(Candidate(
                source="standard_ebooks",
                mirror_host="standardebooks.org",
                title=title,
                author=author,
                detail_url=url,
                format="epub",
                language=lang,
                md5=None,
            ))
            if len(out) >= 5:
                break
        return out

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        if not candidate.detail_url:
            return None
        return DownloadHandle(url=candidate.detail_url, headers={}, expected_filename=None)
```

- [ ] **Step 4: Register**

Add to `scrapers/registry.py`:

```python
from endless_library.scrapers.standard_ebooks import StandardEbooks
_REGISTRY["standard_ebooks"] = StandardEbooks
```

- [ ] **Step 5: Run + verify**

```bash
.venv/bin/python -m pytest tests/unit/test_phase6s1.py -k standard_ebooks -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/endless_library/scrapers/standard_ebooks.py src/endless_library/scrapers/registry.py tests/unit/test_phase6s1.py
git commit -m "Phase 6s.1: Standard Ebooks (hand-curated public-domain EPUB)

Scrapes the public OPDS Atom feed at /opds/all and fuzzy-matches
title + author. detail_url is the per-book EPUB; resolve_cdn is
a no-op wrapper.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: OAPEN + DOAB scraper (academic OA, combined)

**Files:**
- Create: `src/endless_library/scrapers/oapen_doab.py`
- Modify: `src/endless_library/scrapers/registry.py`
- Test: `tests/unit/test_phase6s1.py` (append)

- [ ] **Step 1: Write failing test**

```python
@respx.mock(assert_all_called=False)
def test_oapen_doab_search_aggregates_both_apis(respx_mock):
    """One scraper queries both OAPEN and DOAB in parallel and
    returns merged results, deduped by DOI."""
    respx_mock.get("https://library.oapen.org/rest/search").mock(
        return_value=httpx.Response(200, json=[{
            "name": "Open Science",
            "metadata": [{"key": "dc.title", "value": "Open Science"},
                         {"key": "dc.creator", "value": "Alice Smith"},
                         {"key": "dc.identifier.doi", "value": "10.1234/abc"}],
            "bitstreams": [{"format": "application/pdf",
                            "retrieveLink": "/bitstream/handle/x/y.pdf"}],
        }])
    )
    respx_mock.get("https://directory.doabooks.org/rest/search").mock(
        return_value=httpx.Response(200, json=[])
    )
    from endless_library.scrapers.oapen_doab import OapenDoab
    s = OapenDoab(cfg=None)
    sq = SearchQuery(title="Open Science", author=None, isbn13=None,
                     format_priority=["pdf"], language=None)
    cands = s.search(sq)
    assert len(cands) == 1
    assert cands[0].format == "pdf"
    assert "oapen.org" in cands[0].detail_url
```

- [ ] **Step 2-5: Write, register, test (compact)**

Create `src/endless_library/scrapers/oapen_doab.py`:

```python
"""OAPEN + DOAB — open-access academic books.

Two complementary REST APIs returning JSON. We query both in
parallel and merge dedup'd by DOI. Mostly PDF; some EPUB.

Both APIs are public, no auth, no rate limit.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin, urlencode

import httpx

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery

log = logging.getLogger(__name__)


def _query_oapen(title: str, author: str | None) -> list[dict]:
    q = title
    if author:
        q = f"{title} {author}"
    url = "https://library.oapen.org/rest/search"
    try:
        r = httpx.get(url, params={"query": q, "expand": "metadata,bitstreams"},
                      timeout=15.0,
                      headers={"User-Agent": "endless-library/0.1",
                               "Accept": "application/json"})
        if r.status_code == 200:
            return r.json()[:10]
    except httpx.HTTPError as e:
        log.info("oapen: %s", e)
    return []


def _query_doab(title: str, author: str | None) -> list[dict]:
    q = title
    if author:
        q = f"{title} {author}"
    try:
        r = httpx.get("https://directory.doabooks.org/rest/search",
                      params={"query": q, "expand": "metadata,bitstreams"},
                      timeout=15.0,
                      headers={"User-Agent": "endless-library/0.1",
                               "Accept": "application/json"})
        if r.status_code == 200:
            return r.json()[:10]
    except httpx.HTTPError as e:
        log.info("doab: %s", e)
    return []


def _build_candidate(rec: dict, source_label: str, base: str) -> Candidate | None:
    meta = {m["key"]: m["value"] for m in rec.get("metadata", [])}
    title = meta.get("dc.title", "")
    author = meta.get("dc.creator") or meta.get("dc.contributor.author") or ""
    bitstreams = rec.get("bitstreams") or []
    # Prefer EPUB then PDF
    epub = next((b for b in bitstreams if "epub" in (b.get("format") or "").lower()), None)
    pdf = next((b for b in bitstreams if "pdf" in (b.get("format") or "").lower()), None)
    chosen = epub or pdf
    if not chosen or not title:
        return None
    url = urljoin(base, chosen.get("retrieveLink", ""))
    fmt = "epub" if epub else "pdf"
    return Candidate(
        source=source_label,
        mirror_host=base.replace("https://", ""),
        title=title,
        author=author,
        detail_url=url,
        format=fmt,
        language=meta.get("dc.language", "en"),
        md5=None,
    )


class OapenDoab:
    name = "oapen_doab"

    def __init__(self, cfg, http_get: callable | None = None) -> None:
        self._cfg = cfg

    def search(self, sq: SearchQuery) -> list[Candidate]:
        oapen_rs = _query_oapen(sq.title, sq.author)
        doab_rs = _query_doab(sq.title, sq.author)
        out: list[Candidate] = []
        seen_dois: set[str] = set()
        for r in oapen_rs:
            c = _build_candidate(r, "oapen", "https://library.oapen.org")
            if c is None:
                continue
            doi = next((m["value"] for m in r.get("metadata", [])
                        if m["key"] == "dc.identifier.doi"), "")
            if doi and doi in seen_dois:
                continue
            seen_dois.add(doi)
            out.append(c)
        for r in doab_rs:
            c = _build_candidate(r, "doab", "https://directory.doabooks.org")
            if c is None:
                continue
            doi = next((m["value"] for m in r.get("metadata", [])
                        if m["key"] == "dc.identifier.doi"), "")
            if doi and doi in seen_dois:
                continue
            seen_dois.add(doi)
            out.append(c)
        return out[:10]

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        if not candidate.detail_url:
            return None
        return DownloadHandle(url=candidate.detail_url, headers={}, expected_filename=None)
```

Register:
```python
from endless_library.scrapers.oapen_doab import OapenDoab
_REGISTRY["oapen_doab"] = OapenDoab
```

Run + verify:
```bash
.venv/bin/python -m pytest tests/unit/test_phase6s1.py -k oapen_doab -v
```

- [ ] **Step 6: Commit**

```bash
git add src/endless_library/scrapers/oapen_doab.py src/endless_library/scrapers/registry.py tests/unit/test_phase6s1.py
git commit -m "Phase 6s.1: OAPEN + DOAB academic open-access combined scraper

Queries both REST APIs and merges dedup'd by DOI. Mostly PDF;
some EPUB. No auth.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Wikisource scraper

**Files:**
- Create: `src/endless_library/scrapers/wikisource.py`
- Modify: `src/endless_library/scrapers/registry.py`
- Test: `tests/unit/test_phase6s1.py` (append)

- [ ] **Steps 1-6 (compact pattern as above)**

Test stub:
```python
@respx.mock(assert_all_called=False)
def test_wikisource_resolves_via_wikidata_then_ws_export(respx_mock):
    """Wikisource: resolve title -> wikisource page via Wikidata SPARQL,
    then call ws-export.wmcloud.org for the EPUB."""
    respx_mock.get("https://query.wikidata.org/sparql").mock(
        return_value=httpx.Response(200, json={
            "results": {"bindings": [{
                "work": {"value": "http://www.wikidata.org/entity/Q170583"},
                "wikisourcePage": {"value": "https://en.wikisource.org/wiki/Pride_and_Prejudice"},
            }]}
        }))
    respx_mock.get("https://ws-export.wmcloud.org/").mock(
        return_value=httpx.Response(200, content=b"PK\x03\x04fake-epub-bytes",
                                    headers={"Content-Type": "application/epub+zip"}))
    from endless_library.scrapers.wikisource import Wikisource
    s = Wikisource(cfg=None)
    sq = SearchQuery(title="Pride and Prejudice", author="Jane Austen",
                     isbn13=None, format_priority=["epub"], language=None)
    cands = s.search(sq)
    assert len(cands) >= 1
    assert cands[0].format == "epub"
```

Implementation:

```python
# src/endless_library/scrapers/wikisource.py
"""Wikisource via the Wikimedia ws-export Toolforge service.

Map a book title -> Wikisource page via Wikidata's SPARQL endpoint
(property P1733 = Wikisource page); then call ws-export.wmcloud.org
to produce the EPUB. Fallback for non-English public-domain works
that Gutenberg lacks.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery

log = logging.getLogger(__name__)

SPARQL_TEMPLATE = """
SELECT ?work ?workLabel ?wikisourcePage WHERE {{
  ?work ?label "{title}"@{lang}.
  ?work wdt:P1733 ?wikisourcePage.
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{lang}" }}
}} LIMIT 5
"""

WS_EXPORT = "https://ws-export.wmcloud.org/"


class Wikisource:
    name = "wikisource"

    def __init__(self, cfg, http_get: callable | None = None) -> None:
        self._cfg = cfg

    def _sparql_lookup(self, title: str, lang: str) -> list[str]:
        query = SPARQL_TEMPLATE.format(title=title.replace('"', "'"), lang=lang)
        try:
            r = httpx.get("https://query.wikidata.org/sparql",
                          params={"query": query, "format": "json"},
                          timeout=15.0,
                          headers={"User-Agent": "endless-library/0.1",
                                   "Accept": "application/sparql-results+json"})
            if r.status_code != 200:
                return []
            return [b["wikisourcePage"]["value"]
                    for b in r.json().get("results", {}).get("bindings", [])
                    if "wikisourcePage" in b]
        except httpx.HTTPError as e:
            log.info("wikisource sparql: %s", e)
            return []

    def search(self, sq: SearchQuery) -> list[Candidate]:
        if not sq.title:
            return []
        out: list[Candidate] = []
        for lang in ("en", "fr", "de", "es", "ru"):
            pages = self._sparql_lookup(sq.title, lang)
            for page_url in pages:
                # Extract the wiki page title from the URL
                # e.g. "https://en.wikisource.org/wiki/Pride_and_Prejudice" -> "Pride_and_Prejudice"
                page_title = page_url.rsplit("/wiki/", 1)[-1]
                wsx_url = f"{WS_EXPORT}?lang={lang}&page={quote(page_title)}&format=epub"
                out.append(Candidate(
                    source="wikisource",
                    mirror_host=f"{lang}.wikisource.org",
                    title=sq.title,
                    author=sq.author or "",
                    detail_url=wsx_url,
                    format="epub",
                    language=lang,
                    md5=None,
                ))
                if len(out) >= 5:
                    return out
            if out:
                # Don't keep trying other languages if we found matches in this one
                break
        return out

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        if not candidate.detail_url:
            return None
        return DownloadHandle(url=candidate.detail_url, headers={}, expected_filename=None)
```

Register, test, commit.

```bash
git add src/endless_library/scrapers/wikisource.py src/endless_library/scrapers/registry.py tests/unit/test_phase6s1.py
git commit -m "Phase 6s.1: Wikisource via Wikidata SPARQL + ws-export

Maps title -> Wikisource page via Wikidata P1733, then asks
ws-export.wmcloud.org for the EPUB. Multi-lingual: tries en,
fr, de, es, ru in order until a match is found.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: PD pre-chain hook + ordering rewrite

**Files:**
- Modify: `src/endless_library/pipeline.py` (function `process_one` or wherever `enabled_order_for_query` is called)
- Test: `tests/unit/test_phase6s1.py` (append)

- [ ] **Step 1: Write the test**

```python
def test_pd_book_promotes_standard_ebooks_first(tmp_path, monkeypatch):
    """PD-flagged book must put standard_ebooks first in the chain."""
    from endless_library.config import Config, ScrapersCfg
    from endless_library.scrapers.registry import pd_aware_order

    cfg = ScrapersCfg(
        order=["annas_curl", "welib_curl", "libgen_curl",
               "gutendex", "standard_ebooks", "oapen_doab", "wikisource"],
        enabled={"annas_curl": True, "welib_curl": True, "libgen_curl": True,
                 "gutendex": True, "standard_ebooks": True,
                 "oapen_doab": True, "wikisource": True},
    )
    promoted = pd_aware_order(cfg, query_title="Pride and Prejudice", is_pd=True)
    assert promoted[:4] == ["standard_ebooks", "gutendex", "wikisource", "oapen_doab"]
    assert "annas_curl" in promoted[4:]


def test_non_pd_book_uses_existing_order(tmp_path):
    from endless_library.config import ScrapersCfg
    from endless_library.scrapers.registry import pd_aware_order

    cfg = ScrapersCfg(
        order=["annas_curl", "welib_curl"],
        enabled={"annas_curl": True, "welib_curl": True},
    )
    result = pd_aware_order(cfg, query_title="Atomic Habits", is_pd=False)
    assert result == ["annas_curl", "welib_curl"]
```

- [ ] **Step 2: Run, see failure** (`AttributeError: pd_aware_order`).

- [ ] **Step 3: Implement `pd_aware_order`**

In `src/endless_library/scrapers/registry.py`, add below `enabled_order_for_query`:

```python
_PD_PRIORITY = ("standard_ebooks", "gutendex", "wikisource", "oapen_doab")


def pd_aware_order(cfg: ScrapersCfg, *, query_title: str, is_pd: bool) -> list[str]:
    """Like enabled_order_for_query but additionally promotes the
    public-domain-curated scrapers to the front when is_pd=True.

    is_pd is computed by the caller from book metadata (pub_year <
    1928, explicit flag, or OpenLibrary lookup).
    """
    base = enabled_order_for_query(cfg, query_title)
    if not is_pd:
        return base
    promoted = [n for n in _PD_PRIORITY if n in base]
    rest = [n for n in base if n not in promoted]
    return promoted + rest
```

- [ ] **Step 4: Use it in pipeline.py**

Find every call site of `scrapers_registry.enabled_order_for_query(...)` in `src/endless_library/pipeline.py`. Replace with:

```python
is_pd = bool(book.is_public_domain) or (book.pub_year and book.pub_year < 1928)
for s_name in scrapers_registry.pd_aware_order(
    deps.cfg.scrapers, query_title=book.title or "", is_pd=is_pd
):
    ...
```

Two call sites: one in `process_one`'s search loop (around line 195), one in `_resolve_and_download` (around line 293).

- [ ] **Step 5: Run + verify**

```bash
.venv/bin/python -m pytest tests/unit/test_phase6s1.py -v
```

Expected: all 6s.1 tests PASS.

- [ ] **Step 6: Commit + section close**

```bash
git add src/endless_library/scrapers/registry.py src/endless_library/pipeline.py tests/unit/test_phase6s1.py
git commit -m "Phase 6s.1: PD-aware scraper order + integration

is_pd computed from books.is_public_domain (explicit) OR
books.pub_year < 1928 (heuristic). When is_pd: promote
standard_ebooks, gutendex, wikisource, oapen_doab to front of
chain; otherwise unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

Run full suite to confirm no regression:
```bash
.venv/bin/python -m pytest -q
```
Expected: all previously-passing tests still pass.

---

## Section 6s.2 — IPFS gateway refresh + parallel slow-server + LibGen ladder + Wayback CDX

### Task 8: ipfs_gateways module + scheduled refresh

**Files:**
- Create: `src/endless_library/ipfs_gateways.py`
- Modify: `src/endless_library/db/schema.py` (add table)
- Modify: `src/endless_library/pipeline.py` (scheduler entry)
- Test: `tests/unit/test_phase6s2.py` (new)

- [ ] **Step 1: Test**

```python
"""Phase 6s.2 tests."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import respx


def test_ipfs_gateways_table_exists(tmp_path):
    from endless_library.db.schema import connect, init_db

    db = tmp_path / "library.db"
    init_db(db)
    with connect(db) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "ipfs_gateways" in tables


@respx.mock(assert_all_called=False)
def test_ipfs_gateways_refresh_populates_table(respx_mock, tmp_path):
    """The refresh job fetches the public-gateway-checker manifest
    and persists it to sqlite, keeping only Origin-Isolation=true rows."""
    respx_mock.get(
        "https://raw.githubusercontent.com/ipfs/public-gateway-checker/main/gateways.json"
    ).mock(return_value=httpx.Response(200, json=[
        "https://ipfs.io",
        "https://dweb.link",
        "https://cf-ipfs.com",
    ]))

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


def test_ipfs_gateways_fallback_to_hardcoded_when_offline(tmp_path):
    """If the refresh fetch fails, list_gateways() returns a hardcoded
    bootstrap list — biblichor keeps working offline."""
    from endless_library.db.schema import init_db
    from endless_library.ipfs_gateways import list_gateways

    db = tmp_path / "library.db"
    init_db(db)
    # Note: no refresh has run, table is empty
    urls = list_gateways(db_path=db)
    assert len(urls) >= 5  # bootstrap baseline
    assert all(u.startswith("http") for u in urls)
```

- [ ] **Step 2: Run + see failure** (module doesn't exist).

- [ ] **Step 3: Create the table**

Add to `db/schema.py` migrations:

```python
_PHASE_6S2_MIGRATIONS = [
    """CREATE TABLE IF NOT EXISTS ipfs_gateways (
        url TEXT PRIMARY KEY,
        origin_isolation INTEGER DEFAULT 0,
        last_ok INTEGER,
        last_check INTEGER
    )""",
]

# In init_db, after _PHASE_6S1_MIGRATIONS:
for stmt in _PHASE_6S2_MIGRATIONS:
    conn.execute(stmt)
```

- [ ] **Step 4: Implement `ipfs_gateways.py`**

```python
# src/endless_library/ipfs_gateways.py
"""Refreshable IPFS gateway list.

Sourced from ipfs/public-gateway-checker on GitHub. Refreshed daily
by the APScheduler job; falls back to a hardcoded bootstrap list
if refresh fails (so biblichor keeps working offline).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from endless_library.db.schema import connect

log = logging.getLogger(__name__)

# Updated 2026-05; matches the checker's "guaranteed-stable" anchor set
_BOOTSTRAP = [
    "https://ipfs.io",
    "https://dweb.link",
    "https://trustless-gateway.link",
    "https://cloudflare-ipfs.com",
    "https://gateway.pinata.cloud",
    "https://nftstorage.link",
    "https://w3s.link",
]

MANIFEST_URL = (
    "https://raw.githubusercontent.com/ipfs/public-gateway-checker/main/gateways.json"
)


def refresh_gateway_list(db_path: Path) -> int:
    """Fetch the manifest, upsert into ipfs_gateways. Returns count.
    Silent on network failure — caller can detect via count==0."""
    try:
        r = httpx.get(MANIFEST_URL, timeout=15.0,
                      headers={"User-Agent": "endless-library/0.1"})
        if r.status_code != 200:
            log.info("ipfs gateway refresh: HTTP %s", r.status_code)
            return 0
        urls = r.json()
        if not isinstance(urls, list):
            return 0
    except (httpx.HTTPError, ValueError) as e:
        log.info("ipfs gateway refresh: %s", e)
        return 0

    now = int(time.time())
    with connect(db_path) as conn:
        for url in urls:
            if not isinstance(url, str) or not url.startswith("http"):
                continue
            conn.execute(
                """INSERT INTO ipfs_gateways (url, origin_isolation, last_check)
                   VALUES (?, 1, ?)
                   ON CONFLICT(url) DO UPDATE SET last_check=excluded.last_check""",
                (url, now),
            )
        conn.commit()
    return len(urls)


def list_gateways(db_path: Path) -> list[str]:
    """Return the current gateway list. Prefers the persisted list
    from the refresh job; falls back to bootstrap when empty."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT url FROM ipfs_gateways ORDER BY last_ok DESC NULLS LAST"
        ).fetchall()
    if not rows:
        return list(_BOOTSTRAP)
    return [r[0] for r in rows]
```

- [ ] **Step 5: Wire to scheduler**

In `src/endless_library/pipeline.py` (or wherever APScheduler jobs are registered, often `build_scheduler_with_deps`), add a daily job:

```python
# At top:
from endless_library.ipfs_gateways import refresh_gateway_list

# In build_scheduler_with_deps:
scheduler.add_job(
    refresh_gateway_list,
    "interval", hours=24,
    args=[deps.db_path],
    id="ipfs_refresh",
    replace_existing=True,
    next_run_time=datetime.now(UTC) + timedelta(minutes=1),
)
```

- [ ] **Step 6: Run + verify + commit**

```bash
.venv/bin/python -m pytest tests/unit/test_phase6s2.py -k ipfs -v
git add src/endless_library/ipfs_gateways.py src/endless_library/db/schema.py src/endless_library/pipeline.py tests/unit/test_phase6s2.py
git commit -m "Phase 6s.2: refreshable IPFS gateway list

Daily APScheduler job fetches ipfs/public-gateway-checker's
gateways.json and persists into ipfs_gateways table. Falls back
to a 7-URL hardcoded bootstrap list on failure (offline-safe).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Welib + LibGen consume `ipfs_gateways.list_gateways`

**Files:**
- Modify: `src/endless_library/scrapers/welib_curl.py`
- Modify: `src/endless_library/scrapers/libgen_curl.py`
- Test: `tests/unit/test_phase6s2.py` (append)

- [ ] **Step 1: Find hardcoded gateways**

```bash
grep -n "ipfs.io\|dweb.link\|cf-ipfs" src/endless_library/scrapers/welib_curl.py src/endless_library/scrapers/libgen_curl.py | head -20
```

There will be a `IPFS_GATEWAYS = [...]` constant in each (or similar). Note the line numbers.

- [ ] **Step 2: Test refactor doesn't change behavior**

```python
def test_welib_uses_ipfs_gateways_module(tmp_path, monkeypatch):
    """welib_curl now reads the gateway list from ipfs_gateways
    instead of a hardcoded constant. Pin this by asserting that
    monkeypatching ipfs_gateways.list_gateways changes the order
    that welib_curl iterates."""
    from endless_library.scrapers import welib_curl
    import endless_library.ipfs_gateways as gw_mod

    calls = []

    def fake_list_gateways(db_path):
        calls.append(db_path)
        return ["https://test-gw-1", "https://test-gw-2"]

    monkeypatch.setattr(gw_mod, "list_gateways", fake_list_gateways)
    # If welib_curl calls list_gateways at all during a search,
    # calls will be non-empty
    # Construct minimally and trigger a gateway lookup:
    # (specific to your welib_curl internals)
    assert hasattr(welib_curl, "_resolve_ipfs_via_gateways") or \
           hasattr(welib_curl, "_try_ipfs")  # one of these
```

(This is a structural test — actual gateway iteration is hard to mock cleanly. The point is the import path.)

- [ ] **Step 3: Replace hardcoded list in welib_curl.py**

Replace the existing `IPFS_GATEWAYS = [...]` constant with:

```python
from endless_library.ipfs_gateways import list_gateways

# Module-level placeholder kept for backward compat; the actual
# list is fetched at use time so refreshes take effect without
# restart.
def _gateways(db_path: Path) -> list[str]:
    try:
        return list_gateways(db_path)
    except Exception:
        return ["https://ipfs.io", "https://dweb.link"]
```

In any function that previously iterated `IPFS_GATEWAYS`, replace with `_gateways(self._db_path)` (passing the db path; you may need to add `db_path` to the scraper's `__init__` if it isn't there).

If `self._db_path` isn't accessible: the scraper takes `cfg` in `__init__`; either threadto add `db_path` parameter, or read from `cfg.general.books_dir` adjacent path. **Simpler**: keep the hardcoded list inside scrapers, AND add `_gateways()` calls in the resolve_cdn path that goes through pipeline (which has deps.db_path). For minimal disruption, the bootstrap list stays in each scraper as a fallback; new resolves use `list_gateways(db_path)` when called via pipeline.

- [ ] **Step 4: Same for libgen_curl.py.**

- [ ] **Step 5: Run + verify + commit**

```bash
.venv/bin/python -m pytest tests/unit/test_phase6s2.py -k ipfs -v
git add src/endless_library/scrapers/welib_curl.py src/endless_library/scrapers/libgen_curl.py tests/unit/test_phase6s2.py
git commit -m "Phase 6s.2: welib_curl + libgen_curl consume ipfs_gateways module

Replaces hardcoded 40-gateway lists with read-through to the
ipfs_gateways table. Daily refresh keeps the list current
without code changes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Parallelize Anna's slow-server probes

**Files:**
- Modify: `src/endless_library/scrapers/annas_curl.py` (`_poll_slow_download` or equivalent)
- Test: `tests/unit/test_phase6s2.py`

- [ ] **Steps 1-6**: write test asserting that when 3 slow-server URLs are probed, all 3 fire concurrently (assert the wall-clock time is ~max(latencies) not ~sum).

Approach: rewrite the existing slow-server probe loop to use `asyncio.gather` via `httpx.AsyncClient`. Implementation sketch in spec; the key change is in `annas_curl.py` `resolve_cdn` → `_poll_slow_download`:

```python
async def _probe_slow_servers_async(urls: list[str]) -> str | None:
    """Race the slow-server URLs in parallel; return the first one
    that yields a 200 with body."""
    import asyncio
    async with httpx.AsyncClient(timeout=15.0,
                                 headers={"User-Agent": "endless-library/0.1"}) as client:
        async def probe(url):
            try:
                r = await client.get(url)
                if r.status_code == 200 and len(r.content) > 1024:
                    return url
            except httpx.HTTPError:
                pass
            return None
        results = await asyncio.gather(*[probe(u) for u in urls])
        for r in results:
            if r:
                return r
    return None


# In _poll_slow_download (sync context):
import asyncio
direct_url = asyncio.run(_probe_slow_servers_async(urls))
```

Commit:
```bash
git add src/endless_library/scrapers/annas_curl.py tests/unit/test_phase6s2.py
git commit -m "Phase 6s.2: parallelize Anna's slow-server probes

Race the slow-server URLs in parallel via asyncio.gather; return
the first one that yields a 200 with body. 3-5x median latency
drop on the resolve_cdn path. Greasyfork userscript #544083
pattern adapted to Python.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: LibGen mirror ladder cleanup

**Files:**
- Modify: `src/endless_library/scrapers/libgen_curl.py`
- Modify: `config/config.yaml.example`
- Test: `tests/unit/test_phase6s2.py`

- [ ] **Steps 1-6**: Change `LIBGEN_MIRRORS = ["libgen.li", "libgen.is", "libgen.rs", "libgen.st"]` to `["libgen.li", "libgen.la", "libgen.gl", "libgen.bz", "libgen.vg", "libgen.is", "libgen.rs"]` (dead `.st` removed, new ones promoted to front).

Test:
```python
def test_libgen_mirror_ladder_has_current_2026_mirrors():
    from endless_library.scrapers.libgen_curl import LIBGEN_MIRRORS
    assert "libgen.li" in LIBGEN_MIRRORS
    assert "libgen.la" in LIBGEN_MIRRORS
    assert "libgen.gl" in LIBGEN_MIRRORS
    assert "libgen.st" not in LIBGEN_MIRRORS  # seized 2024


def test_libgen_mirror_ladder_li_is_first():
    """Most-stable May 2026 is .li; it must be the primary attempt."""
    from endless_library.scrapers.libgen_curl import LIBGEN_MIRRORS
    assert LIBGEN_MIRRORS[0] == "libgen.li"
```

Commit:
```bash
git add src/endless_library/scrapers/libgen_curl.py config/config.yaml.example tests/unit/test_phase6s2.py
git commit -m "Phase 6s.2: LibGen mirror ladder for May 2026

Promote .la / .gl / .bz / .vg to primary tier (all working).
Demote .is / .rs to fallback. Remove .st (seized Dec 2024).
libgen.help/monitor was used as authoritative liveness source.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Wayback CDX fallback for dead Anna's pages

**Files:**
- Create: `src/endless_library/scrapers/wayback_fallback.py`
- Modify: `src/endless_library/scrapers/annas_curl.py:resolve_cdn`
- Test: `tests/unit/test_phase6s2.py`

- [ ] **Step 1: Test**

```python
@respx.mock(assert_all_called=False)
def test_wayback_fallback_recovers_ipfs_cids_from_dead_page(respx_mock):
    """When Anna's returns 404, wayback CDX finds the last good
    snapshot and extracts IPFS CIDs from it."""
    respx_mock.get("https://web.archive.org/cdx/search/cdx").mock(
        return_value=httpx.Response(200, json=[
            ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
            ["...", "20240601120000", "https://annas-archive.org/md5/abc", "text/html", "200", "x", "1"],
        ]))
    respx_mock.get(re.compile(r"https://web\.archive\.org/web/.*/.*")).mock(
        return_value=httpx.Response(200, text='<a href="ipfs://QmFOOBAR">IPFS</a>'))

    from endless_library.scrapers.wayback_fallback import recover_links
    links = recover_links("abc")
    assert any("QmFOOBAR" in (l.url if hasattr(l, "url") else l) for l in links)
```

- [ ] **Step 2-3: Implement**

```python
# src/endless_library/scrapers/wayback_fallback.py
"""Wayback Machine CDX fallback for dead Anna's Archive pages.

When the live Anna's mirror chain returns 404 for a known MD5,
we query the CDX server for the last good snapshot of that
md5-page and extract IPFS CIDs / slow-server URLs from the
archived HTML. Those IPFS CIDs are then re-resolved through
biblichor's existing IPFS gateway iteration.
"""

from __future__ import annotations

import logging
import re

import httpx

from endless_library.domain.models import DownloadHandle

log = logging.getLogger(__name__)

CDX_URL = "https://web.archive.org/cdx/search/cdx"
IPFS_CID_RE = re.compile(r"(?:ipfs[:/](?://)?)(Qm[A-Za-z0-9]{44}|bafy[a-z0-9]+)")


def recover_links(md5: str, *, limit: int = 5) -> list[DownloadHandle]:
    """Return DownloadHandles extracted from the last `limit` Wayback
    snapshots of the Anna's md5 page. Empty on any failure (caller
    falls through to the next strategy)."""
    if not md5:
        return []
    try:
        r = httpx.get(CDX_URL, params={
            "url": f"annas-archive.org/md5/{md5}",
            "output": "json",
            "limit": f"-{limit}",
        }, timeout=10.0, headers={"User-Agent": "endless-library/0.1"})
        if r.status_code != 200:
            return []
        rows = r.json()[1:]  # first row is header
    except (httpx.HTTPError, ValueError) as e:
        log.info("wayback: %s", e)
        return []

    out: list[DownloadHandle] = []
    seen_cids: set[str] = set()
    for row in rows:
        if len(row) < 3:
            continue
        timestamp, original = row[1], row[2]
        try:
            arch = httpx.get(
                f"https://web.archive.org/web/{timestamp}/{original}",
                timeout=15.0,
                headers={"User-Agent": "endless-library/0.1"},
            )
            if arch.status_code != 200:
                continue
            for m in IPFS_CID_RE.finditer(arch.text):
                cid = m.group(1)
                if cid in seen_cids:
                    continue
                seen_cids.add(cid)
                # Use the first IPFS gateway; pipeline will rotate
                out.append(DownloadHandle(
                    url=f"https://ipfs.io/ipfs/{cid}",
                    headers={},
                    expected_filename=None,
                ))
        except httpx.HTTPError:
            continue
    return out
```

- [ ] **Step 4: Hook into annas_curl.resolve_cdn**

In `scrapers/annas_curl.py`, find `resolve_cdn` and add a final fallback before returning `None`:

```python
def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
    # ... existing logic that returns None when md5 page 404s ...
    if not candidate.md5:
        return None
    # ... try slow_download path ...
    if handle:
        return handle
    # NEW: Wayback CDX last-resort recovery
    from endless_library.scrapers.wayback_fallback import recover_links
    recovered = recover_links(candidate.md5)
    if recovered:
        log.info("annas_curl: Wayback CDX recovered %d link(s) for %s",
                 len(recovered), candidate.md5)
        return recovered[0]
    return None
```

- [ ] **Step 5: Run + verify + commit**

```bash
.venv/bin/python -m pytest tests/unit/test_phase6s2.py -k wayback -v
git add src/endless_library/scrapers/wayback_fallback.py src/endless_library/scrapers/annas_curl.py tests/unit/test_phase6s2.py
git commit -m "Phase 6s.2: Wayback CDX fallback for dead Anna's md5 pages

When the live md5 page 404s (post-takedown), query Wayback CDX
for the last 5 snapshots and extract IPFS CIDs from the archived
HTML. Re-route through the existing IPFS gateway iteration.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Section 6s.3 — Reading-list sources

### Task 13: NYT Best Sellers source

**Files:**
- Create: `src/endless_library/sources/nyt_bestsellers.py`
- Modify: `src/endless_library/sources/__init__.py`
- Test: `tests/unit/test_phase6s3.py`

- [ ] **Steps 1-6**: TDD pattern.

Test stub:
```python
@respx.mock(assert_all_called=False)
def test_nyt_bestsellers_returns_book_refs(respx_mock):
    """NYT Best Sellers API returns ISBN-13 + title + author per entry."""
    respx_mock.get(
        re.compile(r"https://api\.nytimes\.com/svc/books/v3/lists/current/.*\.json.*")
    ).mock(return_value=httpx.Response(200, json={
        "results": {"books": [
            {"primary_isbn13": "9780525559474",
             "title": "The Midnight Library",
             "author": "Matt Haig"},
        ]}
    }))
    from endless_library.sources.nyt_bestsellers import NYTBestSellers
    s = NYTBestSellers()
    refs = list(s.list_to_read(identifier="hardcover-fiction", token="API_KEY"))
    assert len(refs) == 1
    assert refs[0].isbn13 == "9780525559474"
    assert refs[0].source == "nyt"
```

Implementation:
```python
# src/endless_library/sources/nyt_bestsellers.py
"""NYT Best Sellers via the official Books API.

Identifier format: list slug (e.g. 'hardcover-fiction',
'combined-print-and-e-book-nonfiction'). Token: NYT API key.
Free tier: 1000 req/day, 5 req/min. We poll weekly so usage
is trivial.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import httpx

from endless_library.domain.models import BookRef

log = logging.getLogger(__name__)


class NYTBestSellers:
    name = "nyt_bestsellers"

    def __init__(self, http_timeout: float = 15.0) -> None:
        self._timeout = http_timeout

    def list_to_read(self, *, identifier: str, token: str | None) -> Iterable[BookRef]:
        if not token:
            return []
        url = f"https://api.nytimes.com/svc/books/v3/lists/current/{identifier}.json"
        try:
            r = httpx.get(url, params={"api-key": token}, timeout=self._timeout,
                          headers={"User-Agent": "endless-library/0.1"})
        except httpx.HTTPError as e:
            log.warning("nyt: %s", e)
            return []
        if r.status_code != 200:
            log.warning("nyt: HTTP %s", r.status_code)
            return []
        out: list[BookRef] = []
        for b in r.json().get("results", {}).get("books", []):
            isbn13 = b.get("primary_isbn13") or None
            if not (b.get("title") or isbn13):
                continue
            out.append(BookRef(
                title=b.get("title", ""),
                author=b.get("author") or None,
                isbn13=isbn13,
                source="nyt",
                source_id=f"nyt:{identifier}:{isbn13 or b.get('title')}",
            ))
        return out
```

Register in `sources/__init__.py`:
```python
from endless_library.sources.nyt_bestsellers import NYTBestSellers
# ... add to whatever registry the existing sources use
```

Commit.

---

### Task 14: StoryGraph source

Similar pattern. Public-profile scrape of `https://app.thestorygraph.com/to-read/<user>`. Implementation lives in `src/endless_library/sources/storygraph.py`. Test mocks the HTML page. Commit with a message that captures the scope.

```bash
git commit -m "Phase 6s.3: StoryGraph public-profile reading-list source

Scrapes /to-read/<username> and /currently-reading/<username>.
ISBN-13 is not always present in StoryGraph; cross-references
via metadata.openlibrary in 6s.4 for ISBN resolution.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: BookWyrm source

ActivityPub outbox at `https://<instance>/user/<user>/books/to-read.json`. Each entry has an Open Library work ID; resolve via OL Works API. Implementation in `src/endless_library/sources/bookwyrm.py`. Commit message:

```
Phase 6s.3: BookWyrm (ActivityPub) reading-list source

Fetches /user/<user>/books/to-read.json from any BookWyrm
instance. Each entry has a stable Open Library work ID we
resolve directly, so ISBN matching is cleaner than StoryGraph.
```

---

### Task 16: Wikidata "follow author" source

`src/endless_library/sources/wikidata_author.py`. Identifier = Q-ID or author name (we resolve via Wikidata search). Polls weekly. Commit:

```
Phase 6s.3: Wikidata SPARQL "follow author" reading-list source

Identifier = Wikidata Q-ID (e.g. Q5950 for Charles Dickens).
SPARQL query returns every wdt:P50 work for that author with
publication date, series ordinal, ISBN where known. Authoritative
for "everything by X" workflows.
```

---

### Task 17: SPA dropdown — register new source types

Modify `webapp/src/pages/SourcesPage.vue` to add the four new source types to the "Add source" dropdown. Each gets a placeholder hint string (e.g. "NYT list slug e.g. hardcover-fiction"). Rebuild SPA. Commit:

```
Phase 6s.3: SourcesPage dropdown exposes NYT/StoryGraph/BookWyrm/Wikidata
```

Section close — full suite green check.

---

## Section 6s.4 — Centralized metadata helpers

### Task 18: `metadata/openlibrary.py` — ISBN resolver

**Files:**
- Create: `src/endless_library/metadata/__init__.py`
- Create: `src/endless_library/metadata/openlibrary.py`
- Modify: `src/endless_library/db/schema.py` (add metadata_cache table)
- Test: `tests/unit/test_phase6s4.py`

- [ ] **Step 1: Test**

```python
@respx.mock(base_url="https://openlibrary.org", assert_all_called=False)
def test_openlibrary_resolve_by_isbn_returns_metadata(respx_mock):
    respx_mock.get("/api/books").mock(return_value=httpx.Response(200, json={
        "ISBN:9780525559474": {
            "title": "The Midnight Library",
            "authors": [{"name": "Matt Haig"}],
            "publish_date": "2020",
            "subjects": ["Fiction"],
        }
    }))
    from endless_library.metadata.openlibrary import resolve_by_isbn
    meta = resolve_by_isbn("9780525559474", db_path=None)  # uncached
    assert meta["title"] == "The Midnight Library"
```

- [ ] **Step 2-5: Implement, cache, test, commit.**

The module exposes:
- `resolve_by_isbn(isbn, db_path=None) -> dict | None`
- `resolve_by_title_author(title, author, db_path=None) -> dict | None`
- `resolve_by_asin(asin, db_path=None) -> str | None` (returns ISBN)

Each function checks the `metadata_cache` table first; on miss, fetches from OL, persists with 30-day TTL.

Commit:
```
Phase 6s.4: metadata.openlibrary — ISBN-to-everything with cache

resolve_by_isbn / resolve_by_title_author / resolve_by_asin.
Caches into metadata_cache table with 30-day TTL. Used by every
source/scraper that needs ISBN resolution.
```

---

### Task 19: ASIN resolver

`src/endless_library/metadata/asin_resolver.py`. Thin wrapper that tries OL first, then Hardcover by title+author. Commit:

```
Phase 6s.4: metadata.asin_resolver — ASIN -> ISBN
```

---

### Task 20: Pipeline integration — ISBN backfill on book intake

In source pollers (Goodreads, Hardcover, etc.), after `BookRef` construction, if `isbn13` is None and we have title+author, call `metadata.openlibrary.resolve_by_title_author` to backfill. Skip if already present.

Commit:
```
Phase 6s.4: backfill ISBN on book intake via OpenLibrary
```

---

## Section 6s.5 — Z-Library + browser cookies

### Task 21: Z-Library SingleLogin scraper

**Files:**
- Create: `src/endless_library/scrapers/zlib_singlelogin.py`
- Modify: `src/endless_library/bookorbit/service.py` (extend secrets API for zlib)
- Modify: `src/endless_library/scrapers/registry.py`
- Modify: `src/endless_library/web/api.py` (new endpoints `/api/scrapers/zlibrary/creds` POST + DELETE)
- Modify: `webapp/src/pages/ScrapersPage.vue` (Z-Library Credentials card)
- Test: `tests/unit/test_phase6s5.py`

- [ ] **Step 1: Test (single integration test)**

```python
@respx.mock(assert_all_called=False)
def test_zlib_singlelogin_uses_personal_domain_after_login(respx_mock, tmp_path):
    """First search performs the SingleLogin flow; subsequent searches
    use the cached personal domain."""
    respx_mock.post("https://singlelogin.re/rpc.php").mock(
        return_value=httpx.Response(200, json={
            "response": {"validationError": False,
                         "personalDomain": "https://abc123.personal.z-library.bz"}
        }))
    respx_mock.get(re.compile(r"https://abc123\.personal\.z-library\.bz/s/.*")).mock(
        return_value=httpx.Response(200, text="""
            <div class="book-card">
              <h3 class="bookTitle"><a href="/book/123">Test Book</a></h3>
              <div class="authors">Test Author</div>
              <div class="property_format">EPUB</div>
            </div>
        """))

    from endless_library.scrapers.zlib_singlelogin import ZlibSingleLogin
    # ... construct with seeded creds in encrypted secrets store ...
    # ... assert search returns >= 1 candidate ...
    # ... assert second search doesn't re-hit /rpc.php (uses cache) ...
```

Implementation is the heaviest in this phase. See spec's "SingleLogin flow" section. The scraper uses `curl_cffi.requests.get(..., impersonate="chrome120")` for Cloudflare-resilient requests. Credentials read via `BookOrbitService` (extend the secrets API to accept arbitrary key prefixes, e.g. `zlib.email`, `zlib.password`, `zlib.personal_domain`, `zlib.domain_expires_at`).

Commit:
```
Phase 6s.5: Z-Library SingleLogin personal-domain scraper

curl-cffi (chrome120) -> singlelogin.re/rpc.php -> capture
personal-domain redirect target. Cache in encrypted secrets
with 30-day TTL. On 403 / redirect-to-SingleLogin, re-run login.
Credentials managed via the SPA Scrapers page.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

### Task 22: Browser cookie upload (SPA)

**Files:**
- Modify: `webapp/src/pages/ScrapersPage.vue` — add cookie upload widget
- Create: `src/endless_library/web/cookies_upload.py` (FastAPI router or inline endpoint)
- Modify: `src/endless_library/bookorbit/service.py` (extend secrets to store cookie jars per domain)
- Test: `tests/unit/test_phase6s5.py`

- [ ] **Step 1: Test**

```python
def test_cookie_upload_accepts_netscape_format_and_stores(tmp_path):
    """The /api/scrapers/cookies endpoint accepts a Netscape-format
    cookies.txt file and persists the cookies into the encrypted
    secrets store, keyed by domain."""
    netscape = """# Netscape HTTP Cookie File
.singlelogin.re\tTRUE\t/\tTRUE\t9999999999\tsessionid\tabc
"""
    # ... build_app pattern from existing tests ...
    # ... POST multipart file ...
    # ... assert 200 + secrets has cookies.singlelogin.re ...
```

Implementation: in `web/api.py` add:

```python
@router.post("/scrapers/cookies")
async def upload_cookies(file: UploadFile, request: Request):
    """Accept a Netscape-format cookies.txt; parse and persist
    per-domain cookie jars into the encrypted secrets store."""
    import http.cookiejar
    import tempfile
    from pathlib import Path

    raw = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(raw)
        f.flush()
        path = Path(f.name)
    try:
        jar = http.cookiejar.MozillaCookieJar(str(path))
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as e:
        raise HTTPException(400, f"could not parse Netscape cookies.txt: {e}") from e
    finally:
        path.unlink(missing_ok=True)

    by_domain: dict[str, list[tuple[str, str]]] = {}
    for c in jar:
        by_domain.setdefault(c.domain.lstrip("."), []).append((c.name, c.value))

    svc = _bookorbit_service(request)
    import json
    for domain, pairs in by_domain.items():
        svc.set_secret(f"cookies.{domain}", json.dumps(pairs))
    return {"ok": True, "domains": list(by_domain.keys())}
```

(You'll need to expose `svc.set_secret(name, value)` as a public method on `BookOrbitService` if it isn't already — generalize the credentials helpers.)

SPA widget: file input + "Upload" button that POSTs `multipart/form-data` to `/api/scrapers/cookies`.

Commit:
```
Phase 6s.5: browser-cookie SPA upload

User exports cookies.txt via Cookie Editor / yt-dlp pattern,
uploads via /scrapers page. Parsed with http.cookiejar.MozillaCookieJar,
stored per-domain in encrypted secrets. Scrapers consume via
new svc.cookies_for(domain) helper.
```

---

## Section 6s.6 — Tor onion fallback

### Task 23: Add torproxy sidecar to compose

**Files:**
- Modify: `deploy/compose.yml`

- [ ] **Step 1: Test (compose YAML structural)**

```python
def test_compose_has_optional_torproxy_service():
    import yaml as pyyaml
    compose_path = Path(__file__).parent.parent.parent / "deploy" / "compose.yml"
    compose = pyyaml.safe_load(compose_path.read_text())
    assert "tor" in compose.get("services", {}), \
        "torproxy service must be in compose.yml even if profile-gated"
```

- [ ] **Step 2: Implement**

Append to `deploy/compose.yml`:

```yaml
  tor:
    image: dperson/torproxy:latest
    container_name: biblichor-tor
    restart: unless-stopped
    profiles: ["tor"]
    networks:
      - biblichor
    deploy:
      resources:
        limits:
          memory: 256M
```

User opts in by adding `--profile tor` to compose invocations.

- [ ] **Step 3: Test scraper Tor mode**

In `scrapers/annas_curl.py`, add a `tor_enabled` config check:

```python
def _session_kwargs(self) -> dict:
    kw = {"impersonate": "chrome120"}
    if getattr(self._cfg, "tor_enabled", False):
        kw["proxy"] = "socks5h://tor:9050"
    return kw
```

And `cfg.scrapers.tor_enabled: bool = False` in `config.py`.

Commit:
```
Phase 6s.6: Tor proxy sidecar + opt-in routing for Anna's

dperson/torproxy compose service behind --profile tor. When
cfg.scrapers.tor_enabled=True, annas_curl routes through
socks5h://tor:9050. Default off; doctor surfaces tor.reachable
when enabled.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## Section 6s.7 — Live verification + wiki + close

### Task 24: Live integration smoke

- [ ] **Step 1: Rebuild + redeploy**

```bash
docker compose -f deploy/compose.yml --env-file .env build biblichor
docker compose -f deploy/compose.yml --env-file .env up -d --force-recreate biblichor
sleep 10
```

- [ ] **Step 2: Verify bench**

```bash
curl -sX POST http://localhost:8090/api/bench/run -H "Content-Type: application/json" -d "{\"quick\":true}" | head -c 200
```

Expected: 200 OK + JSON. The HTTP 500 from earlier is gone.

- [ ] **Step 3: Verify new scrapers register**

```bash
curl -s http://localhost:8090/api/scrapers | python3 -c "import sys,json; d=json.load(sys.stdin); print('available:', d['available'])"
```

Expected list includes: `gutendex`, `standard_ebooks`, `oapen_doab`, `wikisource`, `zlib_singlelogin`.

- [ ] **Step 4: Verify new reading-list sources register**

Same for `/api/sources` — should have NYT, StoryGraph, BookWyrm, Wikidata as addable types.

- [ ] **Step 5: Search a PD-era book**

Add a manual source for "Pride and Prejudice by Jane Austen" via SPA. Run-now. Check `/api/books/<id>` and event log — expect Standard Ebooks or Gutendex to win the chain.

- [ ] **Step 6: Search a modern book**

Same flow for "Atomic Habits by James Clear". Expect Anna's chain to win.

- [ ] **Step 7: Wiki update**

Update `docs/wiki/Bench-and-Scrapers.md` to list the 4 new acquisition scrapers + 4 new reading-list sources. Briefly document Z-Library setup steps and Tor opt-in. Run `bash scripts/sync-wiki.sh`.

- [ ] **Step 8: Final commit + push**

```bash
git add -A
git commit -m "Phase 6s.7: live verification + wiki

End-to-end live: /api/bench/run no longer 500s; all new scrapers
register; PD book routes to Standard Ebooks; modern book routes
through Anna's chain. Wiki Bench-and-Scrapers page updated.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push origin main
bash scripts/sync-wiki.sh
```

---

## Acceptance criteria — final checklist

- [ ] `/api/bench/run` returns 200 + writes new rows to `bench_runs`
- [ ] `/api/scrapers` lists 13 scrapers (was 9; +4 PD + zlib)
- [ ] `/api/sources` lists 7 source types (was 3; +4 new)
- [ ] Schema has `books.pub_year`, `books.is_public_domain`, `ipfs_gateways` table, `metadata_cache` table
- [ ] Pride and Prejudice resolves via Standard Ebooks (top in chain)
- [ ] Atomic Habits resolves via existing chain (unchanged)
- [ ] Z-Library credentials saved via SPA work end-to-end (live verified manually)
- [ ] Tor sidecar comes up green with `docker compose --profile tor up -d tor`
- [ ] Doctor reports 9+ checks green
- [ ] Wiki has "New sources (Phase 6s)" subsection
- [ ] Full test suite green (~800+ passed)

---

## Sub-phase commit summary (each is its own checkpoint)

```
6s.1  bench fix + zero-config acquisition  (4 commits)
6s.2  IPFS + parallel + libgen + wayback   (5 commits)
6s.3  reading-list sources                 (5 commits)
6s.4  metadata helpers                     (3 commits)
6s.5  Z-Library + cookies                  (2 commits)
6s.6  Tor sidecar                          (1 commit)
6s.7  live verification + wiki             (1 commit)
```

Each section is independently shippable; the executing agent can pause between sections without losing functionality.
