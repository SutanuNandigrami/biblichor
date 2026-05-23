# Bench and scrapers

How biblichor decides which scraper to use first, and how to
re-rank them when sites change.

## The scraper chain

biblichor tries scrapers in order. The first one that returns a
viable candidate wins; if none do, the book lands in
`needs_review`.

Default order (in `config.yaml` → `scrapers.order`):

```yaml
scrapers:
  order:
    - annas_curl           # curl-cffi, Chrome120 TLS impersonation
    - annas_flaresolverr   # Cloudflare challenge solver
    - annas_cloakbrowser   # stealth headless Chromium
    - annas_playwright     # vanilla Playwright, last resort
    - welib_curl           # Welib + 40-IPFS-gateway fallback
    - welib_playwright     # if welib_curl gets CF Turnstile-walled
    - libgen_curl          # libgen.li/.is/.rs/.st with IPFS fallback
```

You can toggle individual scrapers from the Scrapers page in the
SPA. Drag-to-reorder also works there; changes save to
`config.yaml`.

## Scoring

After scraping, each candidate gets a score. Default weights
(from `config.yaml` → `scoring`):

| Component | Weight |
|---|---|
| ISBN match | 35 |
| Title rapidfuzz | up to 25 |
| Author rapidfuzz | up to 15 |
| Format bonus (epub > azw3 > mobi > pdf) | up to 10 |
| Language match | up to 5 |
| Filesize sanity | small bonus for 0.3-30 MB, penalty outside |
| Scan penalty | -10 if title contains "scan" |
| Derivative skip | hard-skip for "summary", "study guide", "conversation starters" |

Live-edit these from the Scoring page.

Auto-pick logic:

- If top score ≥ `auto_pick_threshold` AND the next-best is more
  than `auto_pick_gap` away → auto-pick
- If top score ≥ `auto_pick_threshold + auto_pick_gap` regardless
  of gap → auto-pick (very confident match)
- Else → `needs_review`
- If top score < `min_score_for_failure` → fail outright

## The bench

`biblichor bench` runs each enabled strategy against the curated
queries in `bench/queries.yaml` and stores results in the
`bench_runs` table.

```bash
biblichor bench --quick     # subset for fast feedback
biblichor bench             # full set (~5 min)
```

Results are visible on the Scrapers page and the Mirrors page.
They feed into the ranking biblichor uses; a strategy that's
consistently slow or failing gets demoted in subsequent runs.

The Scrapers page shows for each strategy:

- Success rate over the last N runs
- Median latency
- Last failure reason

## Mirror auto-discovery

A background job runs every `general.mirror_refresh_hours` (default 6
hours):

1. Scrapes Anna's Wikipedia infobox for the current list of
   mirrors
2. Merges with the hardcoded baseline in `scrapers.annas_mirrors`
3. Health-checks each one
4. Persists the merged + verified list

You can see the Mirrors page for the current ranking. Manual edits
to `scrapers.annas_mirrors` are preserved on refresh.

## When to re-bench

- After Anna's makes a structural change (new domain, new layout,
  Cloudflare config change)
- After you enable / disable scrapers in the SPA
- Once a week as a habit, to catch slow degradations

The bench is cheap to run (the `--quick` flag is ~30s) and the
results inform every subsequent acquisition.

## Welib auth cookie (optional)

Welib gates downloads with Cloudflare Turnstile. If you have a
logged-in Welib session in your browser, you can pass biblichor
the auth cookie:

```bash
# in config/.env
WELIB_AUTH_COOKIE=<your-cookie-string>
```

`welib_curl` will use this header on every request. Without it,
biblichor falls back to `welib_playwright` (slower but works
through Turnstile).

## Format priority

`scrapers.format_priority` controls which file format biblichor
prefers when a candidate has multiple options:

```yaml
scrapers:
  format_priority: [epub, azw3, mobi, pdf]
```

EPUB is always best for Kindle (Calibre converts it cleanly). PDFs
are last because they reflow badly on small Kindle screens.

## Anatomy of a failure

If a book lands in `failed` instead of `needs_review`, the Book
detail drawer shows:

- Every scraper attempted, in order
- HTTP status / exception per attempt
- Top candidates considered (even if below `min_score_for_failure`)
- A `Retry` button that resets the book to `queued`

Failures are usually one of:

- All scrapers hit Cloudflare (FlareSolverr is down or in a bad
  state — `docker compose restart flaresolverr`)
- Welib Turnstile-walled and `welib_playwright` disabled
- Book genuinely doesn't exist on any source biblichor knows about
- ISBN search returned nothing and title-only matches are below
  threshold

## New sources (Phase 6s)

biblichor's acquisition chain and reading-list ingestion both
gained additions in this phase. Everything below ships in the
default container image; nothing requires manual installation.

### Acquisition scrapers (where biblichor looks for the file)

| Scraper | Auth | Notes |
|---|---|---|
| `gutendex` | None | Project Gutenberg via [Gutendex JSON API](https://gutendex.com). Cleanest public-domain source. detail_url IS the CDN URL. |
| `standard_ebooks` | None | [standardebooks.org](https://standardebooks.org) — hand-typeset EPUB classics. Scrapes the public OPDS Atom feed. |
| `oapen_doab` | None | Academic open-access. Queries OAPEN + DOAB REST APIs in parallel, deduped by DOI. Mostly PDF; some EPUB. |
| `wikisource` | None | Multi-lingual via Wikidata SPARQL -> ws-export.wmcloud.org. Best for non-English public-domain works Gutenberg lacks. |
| `zlib_singlelogin` | Z-Library account | Logs into singlelogin.re, caches the per-user personal domain, scrapes search results. Credentials in encrypted secrets store via `POST /api/scrapers/zlibrary/creds`. |

**PD pre-chain hook (automatic).** biblichor checks each book's
metadata for `pub_year < 1928` OR an explicit `is_public_domain`
flag. When set, the four PD-curated scrapers (Standard Ebooks,
Gutendex, Wikisource, OAPEN/DOAB) are promoted to the front of
the chain so a high-quality EPUB wins before Anna's gets a turn.
Modern / copyrighted books use the existing chain unchanged.

### Reading-list sources (where biblichor watches for new titles)

| Source | Identifier | Token |
|---|---|---|
| `nyt` | NYT list slug, e.g. `hardcover-fiction` | NYT API key (free) |
| `storygraph` | username | none (public profiles) |
| `bookwyrm` | `<instance>:<username>` e.g. `bookwyrm.social:alice` | none |
| `wikidata` | Wikidata Q-ID, e.g. `Q36322` for Jane Austen | none |

The Sources page lists these alongside the existing Goodreads /
Hardcover sources. Each source has its own polling schedule
(NYT weekly, the rest daily).

### Novel techniques (improvements to existing scrapers)

- **IPFS gateway auto-refresh** — biblichor pulls the canonical
  gateway list from `ipfs/public-gateway-checker` daily and rotates
  through them. Falls back to a 7-URL hardcoded bootstrap when
  offline. New `ipfs_gateways` sqlite table.
- **Parallel Anna's slow-server probes** — when Anna's
  slow_download page exposes multiple CDN URLs, biblichor races
  them via `asyncio.gather` + HEAD-then-ranged-GET. 3-5x median
  latency drop. Greasyfork userscript #544083 pattern.
- **Wayback CDX fallback** — when Anna's md5 page returns 404,
  query Wayback CDX for the last 5 snapshots and extract IPFS
  CIDs from archived HTML. Hooks into `annas_curl.resolve_cdn`
  as the very-last rung.
- **Centralized OpenLibrary metadata** — new `metadata/` package
  caches OL responses in `metadata_cache` (30-day TTL) so every
  source/scraper gets ISBN-to-everything resolution without
  reimplementing.

### Optional Tor onion fallback

Default OFF. To opt in:

```bash
# Start the torproxy sidecar
docker compose --profile tor up -d tor
# Enable the routing flag
# (Settings page or edit config.yaml directly)
scrapers.tor_enabled: true
```

When enabled, Anna's clearnet chain falls back to the `.onion`
mirror via `socks5h://tor:9050`. Adds 5-30s latency per request;
only useful when clearnet is rate-limited or blocked.

### Z-Library setup

1. Go to `/library` (or `/scrapers` once the dedicated page is
   built) and find the **Z-Library credentials** card.
2. Enter email + password from your Z-Library account.
3. biblichor logs in, captures the per-user personal domain,
   and stores both in the encrypted secrets store.
4. Future searches use the cached personal domain; biblichor
   re-logs-in transparently if it expires.

### Browser cookie upload (advanced)

For sites where biblichor can't easily log in itself (e.g. those
behind Cloudflare Turnstile that needs human interaction once):

1. In your browser, export cookies for the target domain to a
   Netscape-format `cookies.txt` file (the [Cookie Editor](https://cookie-editor.com/)
   extension or yt-dlp's `--cookies-from-browser` produce this).
2. Upload via `POST /api/scrapers/cookies` (file form-field).
3. Cookies are stored per-domain in the encrypted secrets store
   and biblichor adds them to outbound requests for that domain.

## Phase 6v: tagged corpus + scraper health

### Tagged corpus

`bench/queries.yaml` queries each carry a `tags:` list, and a
top-level `corpus_tags:` map routes specialised scrapers to the
subset they can answer:

```yaml
queries:
  - title: "The Pragmatic Programmer"
    tags: [en, modern]
  - title: "Pride and Prejudice"
    author: "Austen"
    tags: [en, pd]
  - title: "হিমু"
    author: "Humayun Ahmed"
    tags: [bn, kindlebangla]

corpus_tags:
  kindlebangla_curl: [kindlebangla]
  gutendex: [pd]
  standard_ebooks: [pd]
  oapen_doab: [pd]
  wikisource: [pd]
```

A scraper absent from `corpus_tags` is general-purpose and sees
every query (Anna's, libgen, welib, zlib). A scraper listed there
only sees queries whose tags intersect the scraper's accepted set
— so `kindlebangla_curl` is no longer benched against "Sapiens"
and `gutendex` is no longer benched against "Project Hail Mary".

The Phase 6v default corpus is 5 modern English titles, 5
Gutenberg-era public-domain classics, and 5 Bengali kindlebangla
slugs pulled from the live production DB. `quick_indices` samples
one of each so `bench --quick` isn't English-only.

### Scraper health: never-tested vs broken

The dashboard used to render a flat "0% success (30d)" for every
scraper, which conflated two very different states: "we benched
it and it failed every time" and "we never benched it at all".
`BenchRunRepo.ever_run(scraper)` returns True iff any row exists
in `bench_runs` for that scraper, and the Scrapers page renders
a **never tested** badge in place of 0% when so.

Companion fields surfaced by `GET /api/scrapers`:

- `in_chain`: `enabled AND in cfg.scrapers.order`. The "enabled"
  toggle alone does not put a scraper in the pipeline chain —
  reordering is also required. This was previously invisible in
  the UI.
- `corpus_tags`: which query tags the scraper is benched against
  (empty list = general-purpose). Rendered as a `corpus: <tags>`
  badge next to specialised scrapers so it's obvious why a given
  pass rate covers only part of the corpus.
- `last_run_at`: timestamp of the most recent bench outcome, drives
  tooltips.

### Test-now button

The Scrapers page exposes a per-row **Test now** button that calls
`POST /api/scrapers/{name}/test_now`. The endpoint picks a single
query from the scraper's corpus (Bengali for `kindlebangla_curl`,
PD classic for `gutendex`, the first general-corpus query for
unfiltered scrapers), runs it, and persists the outcome to
`bench_runs` so the dashboard's success rate updates immediately.
Useful for one-shot "is this scraper alive right now?" probes
without kicking off the full bench.

## Phase 6w

### Bench is now async

`POST /api/bench/run` returns **202 + job\_id** immediately; the caller polls progress via SSE at `GET /api/bench/jobs/{id}/stream`. Jobs can be cancelled with `POST /api/bench/jobs/{id}/cancel`. Each query carries a **20-second per-scraper timeout**; a scraper that fails 3 consecutive queries trips a **circuit breaker** and is skipped for the remainder of that job. Results are persisted to `bench_runs` as they arrive, so a partial run is never lost.

### HTTP foundation

All scrapers now share a `make_client()` factory that returns a **curl-cffi** session configured to impersonate a real Chrome TLS fingerprint. This eliminates the TLS-fingerprint mismatch that caused silent 403s on Cloudflare-protected targets. An **in-process Anubis proof-of-work solver** is wired in as HTTPX middleware; it intercepts `401 Anubis` challenges, solves them with the JS PoW algorithm, and replays the original request transparently. All five HTTP verbs (GET/POST/PUT/PATCH/DELETE) are wrapped.

### New scrapers

| Scraper | Notes |
|---|---|
| **HathiTrust** | ISBN-only lookup; returns public-domain full texts from HathiTrust Digital Library. |
| **DOAB** | REST search over ~90 000 open-access scholarly titles. |
| **Mobilism** | Forum login + per-post Mediafire link resolution; recent-release books are promoted to the front of the resolution chain. |
| **BDeBooks** | Bangla PDFs; uses the new per-source `excluded_categories` denylist (default list excludes Islamic/religious content). |

### Category filtering

`Candidate` now carries a `categories` field populated from source metadata. Each scraper entry in config accepts `excluded_categories`; results matching any listed category are silently dropped before ranking. Default denylists for `bdebooks` and `kindlebangla` exclude Islamic/religious categories.

### Anna's hardening

Mirror rotation across `.gl` / `.li` / `.pm` / `.in` with a **5-minute cool-down** per mirror on failure. A `cf-bypass` sidecar (`sarperavci/cloudflarebypassforscraping`) is added to `compose.yml` as a third anti-bot rung; `annas_curl` falls back to it when the curl-cffi impersonation and Anubis middleware both fail.

### welib

Revived via **Patchright** (Playwright fork with stealth patches). The scraper launches a headless browser session only when the lightweight curl-cffi path fails, keeping normal latency low.

### Open Slum health monitor

`OpenSlumMonitor` polls upstream Open Slum every **10 minutes**. Health state is surfaced in `/healthz` and rendered as a badge on the Scrapers page. Thread-safety enforced with a lock + claim-before-fetch pattern (ultrareview I5).
