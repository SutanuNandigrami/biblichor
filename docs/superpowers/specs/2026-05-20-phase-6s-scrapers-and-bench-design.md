# Phase 6s — New scrapers, sources, techniques, helpers + bench fix

Status: design • Owner: biblichor • Date: 2026-05-20

## Goal

Widen biblichor's "what books can it find" and "what reading lists
can it follow" without changing any existing behavior. Fix the bench
HTTP 500 along the way so we can measure the additions against the
current chain empirically.

## Non-goals

- No legal-risk additions (LCP DRM removal, BitTorrent corpus
  pre-seeding, Mobilism multi-host scraping). Skipped.
- No architectural refactors. New sources follow the existing scraper
  / source interfaces.
- No SPA redesign. The Library, Sources, and Scrapers pages get
  small additions; no new top-level pages.

## Architecture

biblichor already has two pluggable surfaces, and we add one tiny
helper package — no other abstractions:

- **Acquisition scrapers** at `src/endless_library/scrapers/<name>.py`,
  implementing `search(sq) -> list[Candidate]` and
  `resolve_cdn(c) -> DownloadHandle | None`. Registered in
  `scrapers/registry.py`.
- **Reading-list sources** at `src/endless_library/sources/<name>.py`,
  implementing `list_to_read(*, identifier, token) -> Iterable[BookRef]`.
  Wired into the Sources page + scheduler.
- **New `metadata/` package** at `src/endless_library/metadata/` —
  pure helpers (no state, no interface to plug into). Centralizes
  OpenLibrary / Hardcover / Wikidata lookups behind one cached
  facade so individual scrapers don't each reimplement ISBN
  resolution. Consumed by scrapers and the pipeline.

Two new sqlite tables (`ipfs_gateways`, `metadata_cache`) and two
new columns (`books.pub_year`, `books.is_public_domain`) — all
additive, idempotent ALTERs.

## Sub-phase rollout

Each sub-phase is a self-contained ship: green tests, green doctor,
container rebuilt, commit, push. The user can pause between any two
sub-phases without losing functionality.

---

### 6s.1 — Bench fix + zero-config acquisition

**Files**

- Fix: `src/endless_library/bench.py:39` — `bench/queries.yaml`
  resolved as repo-root-relative (was native-cwd-relative); the
  same container-vs-native bug class as the FlareSolverr URL.
- Create: `src/endless_library/scrapers/gutendex.py`
- Create: `src/endless_library/scrapers/standard_ebooks.py`
- Create: `src/endless_library/scrapers/oapen_doab.py`
- Create: `src/endless_library/scrapers/wikisource.py`
- Modify: `src/endless_library/scrapers/registry.py` to register
  the four new scrapers and document the priority order.
- Modify: `src/endless_library/pipeline.py` — add a pre-chain
  hook that boosts the four PD scrapers to the front when the
  book metadata indicates public-domain status (`pub_year < 1928`
  OR `is_public_domain=true` in source metadata).
- Tests: `tests/unit/test_phase6s1.py` — happy-path + 0-result
  for each scraper; respx-mocked.

**Bench fix (concrete)**

```python
# bench.py
def load_queries(path: Path | None = None) -> tuple[...]:
    if path is None:
        path = Path(__file__).resolve().parent.parent.parent / "bench" / "queries.yaml"
    ...
```

Same Path(__file__) anchor pattern the rest of the codebase uses.

**Gutendex sketch**

```python
# scrapers/gutendex.py
class Gutendex:
    name = "gutendex"

    def search(self, sq: SearchQuery) -> list[Candidate]:
        params = {"search": sq.title}
        if sq.author:
            params["search"] = f"{sq.title} {sq.author}"
        r = httpx.get("https://gutendex.com/books", params=params,
                      timeout=15.0)
        if r.status_code != 200:
            return []
        out: list[Candidate] = []
        for book in r.json().get("results", [])[:25]:
            fmts = book.get("formats", {})
            url = (fmts.get("application/epub+zip")
                or fmts.get("application/x-mobipocket-ebook")
                or fmts.get("text/plain; charset=utf-8")
                or fmts.get("text/plain"))
            if not url:
                continue
            out.append(Candidate(
                source="gutendex",
                mirror_host="gutendex.com",
                title=book["title"],
                author=", ".join(a["name"] for a in book.get("authors", [])),
                detail_url=url,
                format=Path(url).suffix.lstrip(".").lower() or "txt",
                language=(book.get("languages") or ["en"])[0],
                md5=None,
            ))
        return out

    def resolve_cdn(self, c: Candidate) -> DownloadHandle | None:
        # detail_url IS the CDN URL for Gutendex
        return DownloadHandle(url=c.detail_url, headers={}, expected_filename=None)
```

**Standard Ebooks sketch**

Per-book scrape of `https://standardebooks.org/ebooks/<author-slug>/<book-slug>`.
The page exposes a `<a class="epub-link">` with the direct EPUB URL.
For search, use their `index.xml` Atom feed and fuzzy-match.

**OAPEN/DOAB sketch**

Two complementary REST APIs returning JSON. Implement as one
scraper that queries both in parallel via `asyncio.gather`,
dedupes by DOI, returns merged results.

**Wikisource WS Export sketch**

Map book → Wikisource page via Wikidata SPARQL
(`?work wdt:P1733 ?wikisource_url`), then call
`https://ws-export.wmcloud.org/?lang=en&page=<title>&format=epub`.
Cache miss latency ~5s. Fallback for public-domain non-English.

**Priority rule (Phase 6s.1.b — pre-chain hook)**

Before iterating the scraper chain, biblichor checks the book's
metadata. The `is_pd` flag is computed from:

1. `books.pub_year < 1928` if set (sources can populate this)
2. OR an explicit `books.is_public_domain` boolean column
   (new column added in this phase, default NULL)
3. OR best-effort lookup against
   `metadata.openlibrary.resolve_by_isbn(isbn).first_publish_year`
   if ISBN is present (cached per Phase 6s.4)

If any of those returns "PD", the iteration order is rewritten:

```
[standard_ebooks, gutendex, wikisource, oapen_doab, <existing chain>]
```

If none can determine PD status, the chain runs as-is (no penalty
to modern books).

Schema migration: `ALTER TABLE books ADD COLUMN pub_year INTEGER`
and `ADD COLUMN is_public_domain INTEGER`. Both nullable.

**Tests**

- Each scraper: empty query → empty list (no crash).
- Each scraper: respx-mocked one-result happy path → 1 Candidate
  with the expected `detail_url` shape.
- `resolve_cdn` returns a usable DownloadHandle for each.
- Pipeline pre-chain hook: PD book promotes the four; non-PD book
  uses existing order.

**Bench fix test**

- `load_queries()` with no args resolves the bundled file.
- `/api/bench/run` returns 200 instead of 500.

---

### 6s.2 — IPFS gateway refresh + parallel slow-server + LibGen ladder + Wayback CDX

**IPFS gateway auto-refresh**

- Create: `src/endless_library/ipfs_gateways.py`
- Modify: `src/endless_library/scrapers/welib_curl.py` and
  `scrapers/libgen_curl.py` to read the gateway list from the new
  module instead of hardcoded constants.
- Modify: `src/endless_library/pipeline.py` scheduler — add a daily
  job that fetches
  `https://raw.githubusercontent.com/ipfs/public-gateway-checker/main/gateways.json`,
  filters to gateways with `OriginIsolation: true`, persists into a
  small sqlite table `ipfs_gateways(url, last_ok, last_check)`. On
  fetch failure (offline, GitHub down), keep the last good list.

**Parallel slow-server probes**

- Modify: `src/endless_library/scrapers/annas_curl.py:_poll_slow_download`
  — current code iterates the (up to 5) slow-server URLs sequentially.
  New: `asyncio.gather(*probes)` with `first_successful()` returning
  the moment any one returns 200 with a body. Use `httpx.AsyncClient`
  for this hot path.
- Drops median latency 3-5x per the greasyfork userscript pattern.

**LibGen ladder cleanup**

- Modify: `config/config.yaml.example` and any default `annas_mirrors`
  / libgen mirror defaults — promote `libgen.li/.la/.gl/.bz/.vg`,
  demote `.rs/.is/.st`. The live `config.yaml` is the user's; we
  only ship new defaults.
- Modify: `scrapers/libgen_curl.py:LIBGEN_MIRRORS` constant.

**Wayback CDX fallback**

- Create: `src/endless_library/scrapers/wayback_fallback.py`
- Modify: `scrapers/annas_curl.py:resolve_cdn` — when the live
  Anna's page 404s for a known md5, call
  `wayback_fallback.recover_links(md5)` which:
  1. Queries `https://web.archive.org/cdx/search/cdx?url=annas-archive.org/md5/<md5>&output=json&limit=-5`
  2. For each capture, fetches the archived HTML at
     `https://web.archive.org/web/<ts>/<url>`
  3. Extracts IPFS CIDs + slow-server URLs from the archived page
  4. Returns them as a fresh `DownloadHandle` candidate list
- Tests: respx-mocked archived page → Wayback yields recovered URLs.

---

### 6s.3 — Reading-list sources

**Files**

- Create: `src/endless_library/sources/nyt_bestsellers.py`
- Create: `src/endless_library/sources/storygraph.py`
- Create: `src/endless_library/sources/bookwyrm.py`
- Create: `src/endless_library/sources/wikidata_author.py`
- Modify: `src/endless_library/sources/__init__.py` to register.
- Modify: `webapp/src/pages/SourcesPage.vue` — add the four new
  source types to the "Add source" dropdown with their identifier
  pattern hints.

**NYT Best Sellers**

- Identifier format: `<list-slug>` (e.g. `hardcover-fiction`,
  `combined-print-and-e-book-nonfiction`)
- Token: NYT API key (free, from developer.nytimes.com)
- Polls weekly (NYT lists update weekly)
- Returns ISBN-13 + title + author per entry; cleanest source in
  the bunch

**StoryGraph**

- Identifier format: `<username>`
- Token: none (public profiles only)
- Polls daily
- Scrape `https://app.thestorygraph.com/profile/<username>` and
  parse the to-read / currently-reading shelves
- Reference: `pip install storygraph-api` (ym496/storygraph-api on
  GitHub); copy the parsing patterns into our existing httpx+BS4
  style so we don't depend on the package's release cadence

**BookWyrm**

- Identifier format: `<instance-host>:<username>` (e.g.
  `bookwyrm.social:alice`)
- Token: none
- Polls daily
- Fetch `https://<instance>/user/<user>/books/to-read.json` (the
  ActivityPub outbox); each entry has an OpenLibrary work ID we
  resolve via the OL Works API → ISBN
- Cleanest of the three because of OL ID stability

**Wikidata "follow author"**

- Identifier format: Wikidata Q-ID (e.g. `Q5950` for Charles Dickens)
  OR author name (we resolve to Q-ID via Wikidata's search API)
- Token: none
- Polls weekly
- SPARQL query against `https://query.wikidata.org/sparql` —
  returns every `wdt:P50` work for that author with publication
  date, series, ISBN where known
- Authoritative for "everything by X" bibliography crawls

---

### 6s.4 — Centralized metadata

**File**

- Create: `src/endless_library/metadata/__init__.py`
- Create: `src/endless_library/metadata/openlibrary.py`
- Create: `src/endless_library/metadata/asin_resolver.py`
- Modify: `src/endless_library/sources/goodreads.py` and friends
  — when a BookRef arrives without ISBN, call
  `metadata.openlibrary.resolve_by_title_author(...)` as a last-
  resort lookup. Skip if ISBN is already present.

**OpenLibrary resolver**

- `resolve_by_isbn(isbn)` → returns `{title, authors, publish_date,
  identifiers, subjects, cover_urls}`
- `resolve_by_title_author(title, author)` → fuzzy search against
  `/search.json` → returns the top match's `work_key`
- `resolve_by_asin(asin)` → uses
  `/api/books?bibkeys=ASIN:<asin>&jscmd=data` to find the ISBN
- Caches in `metadata_cache(key, payload, fetched_at)` sqlite
  table with a 30-day TTL

**ASIN ↔ ISBN**

- `asin_to_isbn(asin)` → tries OpenLibrary first, then Hardcover
  search by title+author with the ASIN's product page as a hint
- Used during Kindle redelivery when only an ASIN is known

---

### 6s.5 — Auth-required acquisition (Z-Library SingleLogin)

**Files**

- Create: `src/endless_library/scrapers/zlib_singlelogin.py`
- Modify: `src/endless_library/bookorbit/service.py` — extend the
  secrets-store helpers to also hold `(zlib_email, zlib_password,
  zlib_personal_domain, zlib_domain_expires_at)`
- Create: `webapp/src/pages/ScrapersPage.vue` — small Z-Library
  Credentials card (email/password fields, "Save" button)
- Create endpoint: `POST /api/scrapers/zlibrary/creds` (same
  encrypted-store pattern as Phase 6p)

**SingleLogin flow**

1. POST `https://singlelogin.re/rpc.php` with `action=login` +
   email/password (curl-cffi impersonate=chrome120)
2. Parse the response → personal domain (`https://<token>.personal.z-library.bz`)
3. Store domain + 30-day expiry in secrets
4. On future search, use the cached domain; if it returns 403 or
   redirects to singlelogin, re-run step 1

**Search/resolve sketch**

```python
class ZlibSingleLogin:
    name = "zlib_singlelogin"

    def search(self, sq: SearchQuery) -> list[Candidate]:
        domain = self._ensure_personal_domain()
        if not domain:
            return []  # creds missing/invalid - silent
        r = self._session.get(f"{domain}/s/{quote(sq.title)}")
        # ... parse result cards ...

    def resolve_cdn(self, c: Candidate) -> DownloadHandle | None:
        # Follow the per-book download chain
```

**Browser cookie upload (6s.5.b)**

- Modify: `webapp/src/pages/ScrapersPage.vue` — add a file-upload
  widget that accepts a Netscape-format `cookies.txt` (the format
  Cookie Editor / yt-dlp export). User exports cookies for
  `singlelogin.re`, `zlibrary.bz`, etc.
- Endpoint: `POST /api/scrapers/cookies` accepts the file, parses
  via `http.cookiejar.MozillaCookieJar`, stores domain-keyed
  cookie tuples in the encrypted secrets store
- Scrapers consume these via a shared `cookies_for_domain(host)`
  helper that returns a `httpx.Cookies` jar

---

### 6s.6 — Tor onion fallback

**Files**

- Modify: `deploy/compose.yml` — add `torproxy` service using
  `dperson/torproxy` image; expose SOCKS5 on internal network at
  `tor:9050`. Resource limits: 256MB RAM cap, restart=unless-stopped.
- Modify: `src/endless_library/scrapers/annas_curl.py` — add a
  fourth domain to the ladder: the Anna's `.onion` URL (read from
  config; default fetched from the project README weekly). When
  using the onion arm, init the httpx client with
  `proxy="socks5h://tor:9050"`.
- Modify: `src/endless_library/bookorbit/doctor.py` — add
  `tor.reachable` check (optional, only if Tor is enabled in
  config)
- Config: `cfg.scrapers.tor_enabled: bool` (default False)

**Why default-off**: Tor adds latency (multi-hop circuit, 5-30s
per request) and most users won't need it. When the clearnet
Anna's ladder is healthy, no Tor needed.

---

### 6s.7 — Live verification + wiki update + commit

- Run the full suite (~770+ tests + 6s additions)
- `docker compose build biblichor && up -d`
- `biblichor bookorbit-doctor` → all checks green
- Manual smoke: add a NYT source, run-now, watch books queue
- Manual smoke: search a PD-era classic, verify Standard Ebooks
  wins the chain
- Wiki update: docs/wiki/Bench-and-Scrapers.md gets a section
  listing every new source with friction tier, polling cadence,
  identifier format
- README: stays slim; just bump the source count

---

## Data layout additions

- `ipfs_gateways(url PRIMARY KEY, origin_isolation INTEGER,
  last_ok INTEGER, last_check INTEGER)` — populated by daily job
- `metadata_cache(key PRIMARY KEY, payload BLOB, fetched_at
  INTEGER)` — 30-day TTL for OL responses
- `secrets`: new rows for `zlib.email`, `zlib.password`,
  `zlib.personal_domain`, `zlib.domain_expires_at`,
  `cookies.<domain>` — all under the existing AES-256-GCM
  envelope from Phase 6p.1

## Risk register

| Risk | Mitigation |
|---|---|
| Z-Library SingleLogin parsing breaks when they change the login flow | Doctor probe + chain falls through to other scrapers silently. User sees a clear "Z-Library re-login failed" event. |
| StoryGraph CAPTCHAs the scraper | We rate-limit hard (1 req / 30s); cookie reuse via 6s.5.b can paper over this; documented fallback is manual CSV export |
| NYT API key rate limits | 1000 req/day / 5 req/min; we poll weekly per list so we use ~7 req/week per list. Trivially under cap. |
| IPFS gateway list changes shape | Pin to a specific commit in `public-gateway-checker`; weekly refresh accepts new schema fields gracefully |
| Tor sidecar adds container surface area | Optional, default off; documented as `tor_enabled: true` opt-in |
| Wayback Machine rate-limits CDX queries | Use sparingly (only on Anna's 404); cache results 24h |

## Testing strategy

- **Unit**: respx-mocked HTTP for each scraper/source. Empty +
  one-result + error paths.
- **Integration**: pin a known-stable book (`Pride and Prejudice`)
  and assert it's findable via at least 3 of the new PD sources.
- **Live**: Phase 6s.7 runs the bench against the new chain and
  attaches the run to the spec as a comment.

## Out of scope (defer)

- BitTorrent DHT direct fetch (architectural debt)
- Mobilism forum scrape (link-rot maintenance burden)
- MyAnonaMouse (private tracker, invite-only)
- LCP DRM removal for Open Library borrowing (legal risk)
- LibraryThing scrape (CAPTCHA-fragile; user can use Goodreads
  CSV import instead)
- Babelio (French; not a current user need)
- OPDS aggregator architectural refactor (premature)

## Acceptance criteria

- `/api/bench/run` returns 200 and produces fresh bench rows
- All 4 new acquisition scrapers register and appear in
  `/api/scrapers`
- All 4 new reading-list sources appear in /sources Add menu
- A PD-era book ("Pride and Prejudice") routes to Standard Ebooks
  ahead of Anna's in the chain
- A modern book ("Atomic Habits") routes through the existing
  chain unchanged
- Z-Library credentials saved via SPA work for one full
  search→download cycle (live verified)
- Tor sidecar comes up green when `tor_enabled: true`
- Wiki has a new "New sources (Phase 6s)" subsection
- Test suite stays green
