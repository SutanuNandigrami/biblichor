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
