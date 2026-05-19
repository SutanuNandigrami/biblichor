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
