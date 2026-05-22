# Architecture

How the pieces fit together.

## Pipeline diagram

```
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  Goodreads RSS   │    │   Hardcover      │    │  Manual / Listopia│
│  Listopia/Series │    │   GraphQL        │    │  / Series source  │
└────────┬─────────┘    └─────────┬────────┘    └─────────┬────────┘
         │                        │                       │
         └────────────┬───────────┴───────────┬───────────┘
                     ▼                        ▼
              ┌──────────────────────────────────────┐
              │   Source poll jobs (one per source)  │
              │   APScheduler, intervals per source  │
              └────────────────┬─────────────────────┘
                               ▼
                      ┌────────────────────┐
                      │   books queue (SQLite WAL)
                      │   states: new → queued → searching →
                      │   needs_review | downloaded → converted →
                      │   sent | failed | skipped
                      └────────┬───────────┘
                               ▼
      ┌────────────────────────────────────────────────────────────┐
      │ scraper strategies, bench-ranked + fallback chain          │
      │                                                            │
      │  Anna's Archive:                                           │
      │   1. annas_curl           — curl-cffi (Chrome120 TLS)      │
      │   2. annas_flaresolverr   — Cloudflare challenge solver    │
      │   3. annas_cloakbrowser   — stealth headless Chromium      │
      │   4. annas_playwright     — vanilla Playwright (last resort)
      │                                                            │
      │  Welib (CF Turnstile-gated):                               │
      │   5. welib_curl + IPFS gateway iteration (40+ gateways)    │
      │   6. welib_playwright (auth-cookie injection optional)     │
      │                                                            │
      │  LibGen mirrors (libgen.li/.is/.rs/.st):                   │
      │   7. libgen_curl + IPFS fallback                           │
      └────────────────────────────┬───────────────────────────────┘
                                   ▼
                ┌──────────────────────────────────────┐
                │ score candidates                     │
                │  ISBN match (35) + title rapidfuzz   │
                │  + author rapidfuzz + format bonus   │
                │  + language bonus + filesize sanity  │
                │  − scan penalty − derivative skip    │
                └──────────────┬───────────────────────┘
                               ▼
                ┌──────────────────────────────────────┐
                │ auto-pick if score ≥ threshold       │
                │ OR (score ≥ threshold + bonus and no │
                │  gap requirement) → needs_review     │
                └──────────────┬───────────────────────┘
                               ▼
       ┌──────────────────────────────────────────────────────┐
       │ download (streaming + MD5 verify + .part resume)     │
       │   → convert (Calibre ebook-convert if needed)        │
       │   → enrich metadata (ebook-meta author/series/tags/  │
       │      ISBN — Kindle uses these for collections)       │
       │   → email to Kindle (aiosmtplib + STARTTLS)          │
       │   → drop into BookOrbit's watched library            │
       └──────────────────────────────────────────────────────┘
                               ▼
                        ┌──────────────┐
                        │    Kindle    │
                        └──────────────┘
                               +
                  ┌────────────────────────────┐
                  │ BookOrbit (reader / Kobo / │
                  │ KOReader / OPDS / stats)   │
                  └────────────────────────────┘
```

## Components

| Component | Process | Notes |
|---|---|---|
| **biblichor** | FastAPI + uvicorn | The acquisition pipeline + scheduler + SPA. Container on port 8090. |
| **bookorbit** | NestJS | Library / reader / sync. Container on port 3000. Talks to bookorbit-db. |
| **bookorbit-db** | Postgres 16 + pgvector | BookOrbit's data store. UID 999. Bind-mounted to `data/bookorbit-db/`. |
| **flaresolverr** | Container | Solves Cloudflare challenges for Anna's Archive. Headless Chromium under the hood. |
| **(optional) clamd** | Container | Virus scanning. Off by default; opt in via `security.require_clamav: true`. |

## Data layout

Everything under `data/`:

| Path | What |
|---|---|
| `data/library.db` | sqlite WAL — books queue + candidates + events + sources + bench history + encrypted secrets |
| `data/books/` | Downloaded files (acquisition staging) |
| `data/secrets/restore.key` | age recovery key (if `backup-key` has been run) |
| `data/secrets/secrets.key` | fallback symmetric key for encrypted secrets store |
| `data/cookies/` | Per-domain FlareSolverr cookies |
| `data/logs/` | Operation logs |
| `data/wiki_annas_domains.json` | Wikipedia mirror refresh cache |
| `data/backups/` | `biblichor backup` output |
| `data/bookorbit-db/` | Postgres data (UID 999) |
| `library/` | BookOrbit-watched library (UID 1000); migrated from `data/calibre-library/` via `biblichor migrate-to-bookorbit` |

## Process and lifecycle

biblichor's FastAPI app runs APScheduler inside its own lifespan:

- Per-source poll jobs (intervals from `general.poll_interval_minutes`
  or per-source override)
- `process_queue` (intervals from `general.process_interval_minutes`)
- `retry_failed` (`general.retry_interval_hours`)
- `mirror_refresh` (`general.mirror_refresh_hours`) — pulls mirror
  list from Wikipedia, merges with hardcoded baseline
- `daily_summary` — once per day at `general.daily_summary_hour_utc`,
  emits a Pushover summary

The scheduler shuts down with the FastAPI lifespan, so a `restart`
is clean.

## Database

7 tables in `data/library.db`, all idempotent ALTER migrations:

| Table | Holds |
|---|---|
| `books` | The queue. One row per book the user wants. |
| `candidates` | Search results scored against each book. Top result is auto-picked or `needs_review`. |
| `events` | Per-book event log shown in the book detail drawer. |
| `source_accounts` | Goodreads / Hardcover / manual source configs. |
| `bench_runs` | Historical bench results for scraper ranking. |
| `mirrors` | Curated + auto-discovered Anna's mirrors with health stats. |
| `secrets` | AES-256-GCM encrypted credentials (BookOrbit admin). |

## Network exposure

| Port | Service | Default binding |
|---|---|---|
| 8090 | biblichor dashboard + API | `0.0.0.0` |
| 3000 | BookOrbit dashboard | `0.0.0.0` |
| 8191 | FlareSolverr | `127.0.0.1` (internal-only) |
| 5432 | bookorbit-db (Postgres) | network namespace only |

Set `TAILSCALE_IP=` in the env to restrict 8090 / 3000 to a single
Tailscale IP.

## UI overhaul (Phase 6t)

biblichor's web UI matches BookOrbit's exact stack — Vue 3.5, Vite 8,
Tailwind v4 (`@tailwindcss/vite`), reka-ui 2.9, vue-router 5,
vite-plugin-pwa 1.3, lucide-vue-next, vue-sonner. Bundled
`@fontsource-variable/inter` ships the font so the dashboard has no
Google Fonts CDN dependency.

**Tokens.** Design tokens are oklch values keyed off a single
`--tint-h` CSS variable on `<html>`. The hue picker on the Settings
page writes that variable and persists it to localStorage, so every
button, badge, focus ring, and bottom-nav active state re-tints
instantly without a reload. Light + dark modes use the same hue with
different L/C bands. Default hue is 265° (BookOrbit blue).

**Responsive shell.** At ≥768 px the layout is a fixed left side-rail
(60 px wide, 9 nav entries). Below 768 px the side-rail is replaced by
a sticky 56 px header plus a fixed bottom nav strip that horizontally
scroll-snaps through the same 9 entries. Pages drop to single-column
layouts, tables collapse to card-lists (Queue / Sources / Mirrors /
Schedule / BookDetail candidates), the Settings card stack becomes
a `<details>` accordion, and the global Run-now button is a
bottom-right FAB. iOS safe-area insets are respected via
`viewport-fit=cover` + `env(safe-area-inset-*)`.

**PWA.** `vite-plugin-pwa` generates a Workbox-backed service worker
plus `manifest.webmanifest`, 64/192/512 px icons, and a maskable
512 px. `/api`, `/ws`, and `/healthz` are denylisted from the
navigation fallback so SPA routing never swallows live API requests.
biblichor is installable as a standalone app on iOS Safari, Android
Chrome, and desktop Chromium browsers; offline cold-starts load the
shell from cache before re-hydrating from the network.

**Heavy lists.** The Logs page uses `vue-virtual-scroller`'s
`DynamicScroller` so 500-event histories stay at 60 fps even on
phones. Scrapers reorder via `vue-draggable-plus` instead of
up/down arrow buttons.

## Phase 6u — Bulk sources, SMTP self-pacing, pipeline hardening

biblichor's source layer can now ingest entire catalogues in one shot,
the pipeline self-paces against SMTP daily caps, and BookOrbit ingest
runs ahead of Kindle delivery so the library always fills even when
email is throttled.

**Bulk source: KindleBangla.** `sources.kindlebangla` walks every
`/category/<slug>?page=N` on kindlebangla.com and yields one BookRef
per book card. Identifier accepts `full` (entire catalogue, default)
or `category:<slug>`. The pipeline detects `source=="kindlebangla"`,
synthesises a Candidate directly from the per-book slug stored in
`books.goodreads_id`, and routes straight to `kindlebangla_curl` —
bypassing the brittle site search that misses long Bengali titles and
slug-disambiguator suffixes (`-1`, `-2`). Auto-pick floor and threshold
are forced to 0 for this source because the Bengali titles bottom-out
the Latin-tuned scorer.

**SMTP daily-cap gate.** `cfg.smtp.daily_cap` (default 80, 0 disables)
governs how many Kindle SMTP sends the pipeline emits per rolling 24h.
`smtp_rate.quota_status(db_path, daily_cap)` counts `kind='send'`
events in the window; when the cap is reached, `process_one` emits a
`send_deferred` event and returns `'deferred'` instead of calling
`send_to_kindle`. Server-side throttle responses (Gmail's 421 / 452
/ 550 5.4.5 / 4.7.28) are also caught as `KindleRateLimited` and
deferred the same way. `/healthz` now includes
`smtp: {sent_24h, cap, remaining, exhausted}` and a mailbox pill in
the desktop top header polls it every 60s.

**GUI SMTP provider swap.** Settings page exposes a Provider preset
dropdown (Gmail, Workspace, Brevo 300/day free, SendGrid 100/day free,
AWS SES 200/day, Mailgun, Custom). Picking a preset fills host / port
/ starttls / daily_cap / max_attachment_mb. User still types their own
user + password. Settings POST accepts `smtp_starttls`,
`smtp_daily_cap`, and `smtp_max_attachment_mb` so the dropdown is the
only place these values are edited.

**BookOrbit before SMTP.** The hand-off to BookOrbit's library is now
sequenced *before* the SMTP gate. When the daily cap is hit, the book
still lands in the library; only the Kindle email is deferred. Drop
is idempotent across deferred retries via a `kind='bookorbit'` event
check.

**Pipeline concurrency hardening (the kindlebangla flood found these):**

- *Atomic claim.* `_process_job` (every 7 min) and `_retry_job`
  (every 4 h) both call `process_queue`. APScheduler's `max_instances=1`
  is per-job, so they overlap. `BookRepo.claim_for_processing(book_id)`
  is a conditional `UPDATE books SET status='searching' WHERE id=?
  AND status IN ('queued','failed','needs_review')`; if zero rows
  match, the worker returns the `'in_flight'` sentinel. Called BEFORE
  the resume path so two cycles can never Kindle-send the same
  already-downloaded book.
- *UUID-stamped .part files.* Downloads stage to
  `<name>.<ext>.part.<12-hex-uuid>` per attempt. Concurrent workers
  can never share a `.part` filename.
- *httpx exceptions wrapped.* Any `httpx.HTTPError` raised mid-stream
  (ReadError, RemoteProtocolError, ReadTimeout, ConnectError) is
  re-raised as `DownloadError` so the pipeline's narrow handler
  catches them and treats them as recoverable. Empty-body sentinel
  raises `DownloadError("empty body … likely quota / private link")`
  rather than passing 0 bytes to the unpack step.
- *Smarter zombie sweep.* `reset_zombies` now returns stuck-in-flight
  books to `'queued'` (not `'failed'`); the resume path picks them up
  via `downloaded_at + file_path` without burning their attempts
  budget.
- *Collision-proof unpack filename.* `safe_extract_zip` /
  `safe_extract_rar` used the inner archive member name as the
  destination, which collided across different books with the same
  inner filename and silently overwrote earlier extractions. Now uses
  the outer downloaded filename (always unique per provider slug or
  md5); the original archive is kept as `<name>.<ext>.orig` for
  forensics.
- *Bengali directory names.* `_check_member_safe` now skips directory
  entries from the extension allowlist; Python's `Path.suffix`
  mis-parses names like `Ph.D. বল্টুভাই/` and trips the allowlist
  on the "extension" `". বল্টুভাই"`.
- *RAR support.* `unrar-free` + `libarchive-tools` in the runtime
  image plus `rarfile>=4.2` in pyproject deps. kindlebangla's Drive
  payloads are RAR-wrapped EPUBs; the existing
  `security.safe_extract_rar` code path now has the binaries it
  needs.

**Queue pagination.** `/api/books` accepts `limit` + `offset` and
returns a real SQL-filtered `total`. QueuePage exposes a
10/20/50/100 page-size dropdown (persisted to localStorage) and
first/prev/next/last controls. Status dropdown is seeded with the
full lifecycle (`queued`, `searched`, `picked`, `downloading`,
`converting`, `sending`, `sent`, `failed`, `needs_review`, `skipped`,
`deferred`) so filters stay available even when the page slice
doesn't contain them.

**Desktop theme toggle.** Top header now carries the same moon/sun
toggle as `MobileHeader`, sharing the `biblichor.theme` localStorage
key.

**Source allowlist via registry.** The `/api/sources` POST handler
used to allowlist 5 hardcoded source names, silently rejecting all
the Phase 6s.3 and 6u additions with HTTP 400. It now reads
`sources/registry._SOURCES` directly, so any source the registry
can build is fair game.

**Test count: 851 passed / 2 skipped.**

## Phase 6v — corpus-aware bench, scraper transparency, BookOrbit upgrade

**Per-scraper bench corpus.** `bench/queries.yaml` queries now carry
a `tags` field, and the file has a top-level `corpus_tags` map that
routes specialised scrapers to the subset they can answer. The
default corpus picks up 5 modern English titles, 5 Gutenberg-era
public-domain classics, and 5 Bengali kindlebangla slugs pulled from
the live production database. Routing:

| Scraper                     | Sees queries tagged |
|-----------------------------|---------------------|
| `kindlebangla_curl`         | `kindlebangla`      |
| `gutendex`                  | `pd`                |
| `standard_ebooks`           | `pd`                |
| `oapen_doab`                | `pd`                |
| `wikisource`                | `pd`                |
| `annas_curl` / `libgen_curl` / `welib_*` / `zlib_singlelogin` | unfiltered (general-purpose) |

`bench.queries_for_scraper(qs, name, corpus_tags)` is the filter;
`run_bench` loads `load_corpus_tags()` automatically when not given
one, so legacy callers don't need a migration. `load_queries`
signature is unchanged.

**Scraper health distinguishes never-tested from broken.**
`BenchRunRepo.ever_run(scraper)` returns True iff there is at least
one recorded outcome for that scraper, regardless of pass/fail.
`GET /api/scrapers` now also returns `ever_run`, `last_run_at`,
`in_chain` (`enabled AND in cfg.scrapers.order`) and `corpus_tags`
per scraper. The Scrapers page renders a "never tested" badge in
place of a misleading 0% rate, shows an explicit "in chain" /
"not in chain" indicator alongside the enabled toggle (the former
"enabled" badge alone hid the case where a scraper is toggled on
but absent from `order`, in which case the pipeline silently never
calls it), and exposes a per-row "Test now" button. `POST
/api/scrapers/{name}/test_now` runs one query the scraper actually
accepts (Bengali for `kindlebangla_curl`, PD classic for `gutendex`,
etc.) and persists the outcome to `bench_runs`.

**Safe BookOrbit upgrade.** The Settings page carries an upgrade card
that walks the user through version → preflight → apply with full
rollback. The flow:

1. `GET /api/bookorbit/upgrade/status` queries BookOrbit's own
   `/api/v1/app-info` for current + latest version (BookOrbit polls
   upstream itself, so we don't run a parallel GitHub check),
   fetches the release notes from GitHub's releases API, and
   reports whether `/var/run/docker.sock` is reachable from inside
   biblichor.
2. `POST /api/bookorbit/upgrade/preflight` runs seven checks:
   `docker.socket`, `image.pull` (heavy — actually pulls the
   candidate image), `image.inspect`, `disk.free` (≥ 2 GB),
   `db.dumpable` (`pg_isready`), `changelog.safe` (scans release
   notes for DANGER_WORDS: `breaking change`, `manual migration`,
   `data loss`, ...), `backup.dir_writable`. On pass, a 15-minute
   token + target + expiry is stashed in `app.state.upgrade_state`.
3. `POST /api/bookorbit/upgrade/apply` requires that token and
   executes: `token.validate` → `db.backup` (pg_dump | gzip into
   `/data/backups/bookorbit-pre-<target>-<ts>.sql.gz`) →
   `compose.swap_image` (surgical regex on `deploy/compose.yml`'s
   bookorbit service image line; biblichor / db / flaresolverr
   untouched) → `compose.up_bookorbit` (`docker compose up -d`)
   → `health.poll` (`GET /api/v1/health` until DB up or 90s) →
   `version.verify`. Any failure after `compose.swap_image` fires
   rollback: revert the SHA, `docker compose up -d bookorbit` to
   bring back the prior container. Backups are kept indefinitely.

**Docker access.** `deploy/compose.yml` bind-mounts
`/var/run/docker.sock` + `deploy/` + the host `.env` into
biblichor. `group_add: ["${DOCKER_GID:-999}"]` lets the in-container
UID 1000 write to the socket; users set `DOCKER_GID` in `.env`
(check with `getent group docker | cut -d: -f3`). The Dockerfile
installs the static `docker` CLI binary and the `docker compose`
plugin via the official tarball — multi-arch via the buildx
`TARGETARCH` arg (`x86_64` ↔ `aarch64`). The compose plugin lives
at `/usr/local/lib/docker/cli-plugins/docker-compose`.

**Privilege boundary.** A compromise of biblichor with docker socket
access can manage every container on the host. The bind mount is
opt-in via `compose.yml`; the UI degrades to a "docker socket not
reachable" banner with explicit remediation steps when omitted, and
the rest of biblichor keeps working.

**Test count: 893 passed.**
