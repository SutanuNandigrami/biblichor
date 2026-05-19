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
