# Configuration

biblichor's behavior is controlled by three things: two `.env` files
(secrets) and one `config.yaml` (operational settings). The
Settings page in the SPA edits `config.yaml` live; secrets stay in
the `.env` files.

## The two `.env` files

biblichor maintains **two** `.env` files with non-overlapping
responsibilities:

| File | Purpose | Who writes it |
|---|---|---|
| `<repo>/.env` | **docker compose** secrets (`BOOKORBIT_*`, `GMAIL_*`, `KINDLE_EMAIL`, etc). Read by `docker compose --env-file` for variable interpolation in `compose.yml`. | `bootstrap.sh` |
| `<repo>/config/.env` | **biblichor application** secrets (`SMTP_*`, `KINDLE_EMAIL`, `WELIB_AUTH_COOKIE`, `BOOKORBIT_URL`). Read by `endless_library.config.load_config()` for env-overrides over `config.yaml`. | biblichor itself (via `save_config`) |

They overlap on a few keys (`KINDLE_EMAIL`, `BOOKORBIT_URL`) but
never conflict: docker compose interpolates its env into the
container's process environment, and biblichor reads from that same
process environment.

**Source of truth**: docker compose `.env` for first-run secrets;
biblichor's `config/.env` for runtime tuning.

## `config/config.yaml`

Operational settings, live-editable from the Settings page.
Highlights:

```yaml
general:
  poll_interval_minutes: 60       # default for new sources
  process_interval_minutes: 10    # how often to drain the queue
  retry_interval_hours: 6         # retry pass over failed/pending
  mirror_refresh_hours: 6         # Wikipedia → annas_mirrors refresh
  daily_summary_hour_utc: 14      # Pushover daily tally
  max_attempts: 5
  books_dir: ./data/books         # where files land
  auto_pick_threshold: 70         # min score to auto-pick a candidate
  auto_pick_gap: 10               # if next-best is within this gap, → needs_review
  min_score_for_failure: 40       # below this → fail outright

scrapers:
  order: [annas_curl, annas_flaresolverr, annas_cloakbrowser,
          welib_curl, welib_playwright, libgen_curl]
  enabled: { annas_curl: true, ... }
  format_priority: [epub, azw3, mobi, pdf]
  annas_mirrors:
    - https://annas-archive.gl
    - https://annas-archive.pk
    - https://annas-archive.gd

scoring:
  isbn_match: 35
  title_weight: 25
  author_weight: 15
  format_bonus: { epub: 10, azw3: 9, mobi: 8, pdf: 5 }

bookorbit:
  enabled: true
  url: http://bookorbit:3000        # INTERNAL API URL (see below)
  library_root: /library            # container path biblichor sees
  organization_mode: book_per_folder
  library_id: "1"                   # populated by bookorbit-setup
```

The Settings page writes back to this file. Re-reads happen on
restart.

## Host filesystem requirements

BookOrbit's container runs as **UID 1000** (`PUID=1000`,
`PGID=1000` in compose env). biblichor's container also runs as UID
1000. Postgres runs as UID 999 inside its own container.

The shared library bind-mount `./library` and biblichor's
`./config`, `./data` (except `data/bookorbit-db`) must be readable
+ writable by UID 1000 on the host.

- On Ubuntu/Debian cloud images, the `ubuntu` user is UID 1000 — no
  action needed.
- On custom cloud images, NAS, or Synology, either override
  `PUID/PGID` in `.env` to match your user, OR fix ownership with:

  ```bash
  deploy/fix-perms.sh
  ```

  The helper chowns `config/`, `library/`, and biblichor-owned
  subdirs of `data/` to UID 1000, while restoring
  `data/bookorbit-db` to UID 999. **Do NOT use a blanket
  `sudo chown -R 1000:1000 .` from the repo root** — it will brick
  postgres.

## How BookOrbit's URL is resolved (internal vs external)

biblichor splits BookOrbit's URL into two distinct concerns:

| Setting | What it's for | Default |
|---|---|---|
| `BOOKORBIT_URL` (`.env` / `config.yaml`) | INTERNAL API URL biblichor uses to call BookOrbit | `http://bookorbit:3000` (compose service name) |
| `BOOKORBIT_EXTERNAL_URL` (env, optional) | USER-FACING URL the SPA links to | unset — SPA auto-derives from `request.url.hostname` |

The SPA's "Open BookOrbit" / OPDS / Kobo / KOReader links use the
hostname **the user typed into their browser**, with the BookOrbit
port appended. Tailscale names, LAN IPs, and `localhost` all work
without per-device config.

Set `BOOKORBIT_EXTERNAL_URL` ONLY for reverse-proxy / split-DNS
setups where BookOrbit is published at a different hostname than
biblichor. 99% of users should leave it unset.

## Tag-pinning the BookOrbit image

`deploy/compose.yml` pins BookOrbit by **sha256 digest**:

```yaml
image: ghcr.io/bookorbit/bookorbit@sha256:<…>
```

GHCR doesn't publish semver tags for BookOrbit (the registry only
has `:latest`), so digest pinning is the only way to get
reproducibility. To bump:

1. `docker pull ghcr.io/bookorbit/bookorbit:latest`
2. `docker inspect ghcr.io/bookorbit/bookorbit:latest --format '{{index .RepoDigests 0}}'`
3. Paste the new digest into `compose.yml`.
4. **Run `biblichor bookorbit-doctor`** (or click "Run doctor" in
   the SPA) — it probes the 5 endpoints biblichor depends on
   (`/auth/setup-status`, `/auth/login`, `/libraries`, `/scanner`,
   `/health` + OPDS) to catch any DTO drift.
5. `docker compose pull && docker compose up -d bookorbit`.
6. Run the doctor again to confirm post-upgrade.

If the doctor flags drift, downgrade to the previous digest
(`git log -p deploy/compose.yml`) and open an issue.

## Optional: enabling BookOrbit metadata providers

BookOrbit ships with 9 metadata providers (Goodreads, OpenLibrary,
Hardcover, Google Books, Amazon, iTunes, Audible, AudNexus,
ComicVine), all **disabled by default**. biblichor deliberately
doesn't enable them programmatically, but you can turn them on in
BookOrbit's UI for richer cover art, series ordering, and
descriptions.

**Critical:** if you enable any metadata provider, set the field
rule to **"Fill missing only"** (BookOrbit Settings → Metadata
Preferences). The default ("Overwrite if provided") will silently
clobber Bengali / CJK / Cyrillic titles biblichor wrote during
enrichment, because providers like Goodreads only have Latin
transliterations of those titles.

Recommended:

- **OpenLibrary** + **Goodreads** — best coverage, no API key
  needed for OpenLibrary, Goodreads needs a free account
- **Hardcover** — best for newer / indie books
- **Skip Amazon + iTunes** unless you read mainstream English
  bestsellers

biblichor sets a per-library **auto-scan cron** (`0 * * * *`) on
first-run setup. The watcher catches every real-time drop; the
hourly cron is belt-and-braces for missed events on network shares
or during container restarts.
