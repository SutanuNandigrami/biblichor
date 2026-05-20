# BookOrbit integration

BookOrbit is biblichor's reader, sync, and library UI. biblichor
runs the acquisition pipeline; BookOrbit serves the books.

## What biblichor uses BookOrbit for

| Surface | What it does |
|---|---|
| **In-browser reader** | EPUB / PDF reader at `/read/{bookId}/{fileId}` |
| **OPDS catalog** | `/api/v1/opds` — works with KOReader, Thorium, Moon+ Reader, Marvin, Aldiko |
| **Kobo sync** | `/api/v1/kobo/{deviceToken}` — registers Kobo devices, pushes new books over Wi-Fi |
| **KOReader sync** | Two-way reading progress sync via OPDS extensions |
| **Reading statistics** | Heatmaps, streaks, pages-per-day |
| **Watched library** | Files biblichor drops into the bind mount are auto-ingested via embedded metadata |

## URL model

biblichor splits BookOrbit's URL into two distinct concerns:

| Setting | What it's for | Default |
|---|---|---|
| `BOOKORBIT_URL` (`.env` / `config.yaml`) | INTERNAL API URL biblichor uses to call BookOrbit | `http://bookorbit:3000` (compose service name) |
| `BOOKORBIT_EXTERNAL_URL` (env, optional) | USER-FACING URL the SPA links to | unset — SPA auto-derives from `request.url.hostname` |

The SPA's user-facing links use the **hostname the user typed into
their browser** + the BookOrbit port. Tailscale names, LAN IPs, and
`localhost` all work without per-device config. Set
`BOOKORBIT_EXTERNAL_URL` only if your reverse-proxy or split-DNS
setup publishes BookOrbit at a different hostname than biblichor.

## The setup wizard

The Library page (`/library`) shows a **First-run setup needed**
card whenever BookOrbit reports `needsSetup=true`. The wizard:

1. Mints a fresh 64-char URL-safe setup token (via
   `/api/bookorbit/setup-token`)
2. Collects admin username / display name / email / password +
   library root
3. Calls BookOrbit's `/auth/setup`, then logs in, then creates a
   watched library named `biblichor` at the library root path
4. Stores the credentials encrypted in `library.db` (see
   [Backups → Encrypted secrets](Backups#encrypted-secrets-store))
5. Flips `bookorbit.enabled = true` in `config.yaml`

After this, the Library page shows the sync surfaces (OPDS, Kobo,
KOReader, Statistics) plus three action buttons: **Scan now**,
**Run doctor**, **Update creds**.

## The doctor

`biblichor bookorbit-doctor` (also reachable as a button on the
Library page) runs 6 probes:

| Check | What it verifies |
|---|---|
| `health.reachable` | BookOrbit responds to `GET /api/v1/health` |
| `health.database` | Embedded `info.database.status` is `"up"` |
| `setup_status.dto` | `GET /auth/setup-status` returns the expected `needsSetup` key |
| `library_root.exists` | The path in `cfg.bookorbit.library_root` actually exists inside biblichor's process — catches container vs host path mismatches |
| `auth.creds_provided` | If credentials are stored, login + `GET /libraries` work and the biblichor library shows up in the list |
| `opds.reachable` | `GET /api/v1/opds` returns 401/403 (endpoint exists, just unauthenticated) |

Run the doctor after **any** BookOrbit upgrade. If a future
BookOrbit version changes one of these shapes, the doctor catches
it before user-facing breakage.

## The BookOrbit API surface biblichor depends on

BookOrbit's REST API is **not publicly documented**, so biblichor
relies on just these endpoints (discovered by reading the NestJS
server modules). Pinned by `bookorbit-doctor`:

| Endpoint | Used for | If it changes shape |
|---|---|---|
| `GET /api/v1/health` | liveness probe | doctor + healthcheck fail loudly |
| `GET /api/v1/auth/setup-status` | first-run gate | wizard + doctor fail |
| `POST /api/v1/auth/setup` | bootstrap admin (x-setup-token) | wizard fails |
| `POST /api/v1/auth/login` | JWT for follow-up calls | scan + authenticated doctor fail |
| `POST /api/v1/libraries` | create watched library | wizard fails |
| `GET /api/v1/libraries` | doctor probe + sanity | doctor flags drift |
| `POST /api/v1/scanner/libraries/{id}/scan` | manual scan trigger | Scan button + `migrate --trigger-scan` fail |
| `GET /api/v1/opds` | OPDS surface in SPA | doctor flags |

## Image pinning

`deploy/compose.yml` pins BookOrbit by **sha256 digest** because
GHCR doesn't publish semver tags. To bump:

1. `docker pull ghcr.io/bookorbit/bookorbit:latest`
2. `docker inspect ghcr.io/bookorbit/bookorbit:latest --format '{{index .RepoDigests 0}}'`
3. Paste the new digest into `compose.yml`.
4. **Run the doctor** to confirm no DTO drift.
5. `docker compose pull && docker compose up -d bookorbit`.
6. Run the doctor again post-upgrade.

If the doctor flags drift, downgrade with `git log -p
deploy/compose.yml` and open an issue.

## Metadata providers

BookOrbit ships with 9 metadata providers (Goodreads, OpenLibrary,
Hardcover, Google Books, Amazon, iTunes, Audible, AudNexus,
ComicVine), all **disabled by default**. Enable them in BookOrbit's
own Settings UI for richer cover art and series ordering.

**Critical rule:** set the field rule to **"Fill missing only"**
(BookOrbit Settings → Metadata Preferences). The default
("Overwrite if provided") silently clobbers Bengali / CJK /
Cyrillic titles biblichor wrote during enrichment, because
providers like Goodreads only have Latin transliterations.

Recommended providers:

- **OpenLibrary** — no API key, broad coverage
- **Goodreads** — needs a free account
- **Hardcover** — best for newer / indie books
- Avoid Amazon + iTunes unless you read mainstream English
  bestsellers; they're biased and require an Amazon/Apple ID

biblichor sets a per-library **auto-scan cron** (`0 * * * *`) on
first-run setup. The real-time watcher catches every drop; the
hourly cron is belt-and-braces for missed events on network shares
or during container restarts.

## Credentials management

biblichor's GUI handles BookOrbit credentials end-to-end. You
never need to type a current password or look at any `.env` file.

### How biblichor knows the current password

At container startup, biblichor reads `BOOKORBIT_ADMIN_PASSWORD`
from its environment (passed in via `compose.yml`) and, if it
doesn't already have credentials in its encrypted store, seeds
them silently. After that, the encrypted store is the source of
truth — biblichor never reads the env var again unless the store
is wiped.

### Change password (one field)

The **Change password** card on the Library page asks for one
thing: the new password (with a confirm field). Under the hood,
biblichor:

  1. logs into BookOrbit using its stored credentials
  2. mints a one-time reset token via
     `POST /api/v1/users/{id}/reset-password`
  3. applies the new password via
     `POST /api/v1/auth/reset-password` with that token
  4. updates its encrypted store so Scan / Doctor keep working

You pick a password you'll remember; biblichor handles the rest.

### Stored creds (advanced)

**What it does**

The Stored creds card directly writes or wipes the
`(username, password)` pair biblichor uses to authenticate with
BookOrbit for Scan and Doctor. Stored encrypted in `library.db`.
**It does NOT change BookOrbit's password** — for that, use
Change password.

**When you need it (rare)**

- You changed BookOrbit's admin password through BookOrbit's own
  dashboard (not via biblichor). Biblichor's stored copy is now
  stale and Scan / Doctor will fail with *"Invalid credentials"* —
  enter the new password here to bring biblichor back in sync.
- You want to wipe stored creds for some reason
  (decommissioning, moving hosts, debugging).

**When you don't need it (most cases)**

- First container start — auto-seeded from compose env; creds
  populate themselves.
- After clicking Change password — rotation flow updates stored
  creds automatically.
- BookOrbit upgrades — stored creds are unaffected.

**What happens on Save**

biblichor stores the username + password you type (encrypted)
and Scan / Doctor immediately start using them. No call is made
to BookOrbit — if the password you enter doesn't match
BookOrbit's actual current password, Scan / Doctor will fail
with 401 next time. The "Stored creds" card is purely a local
sync surface.

**What happens on Clear stored creds**

Stored creds are removed. Scan and authenticated Doctor checks
stop working until one of:

- the container restarts and re-seeds from the
  `BOOKORBIT_ADMIN_PASSWORD` env var
- you Save new creds in this card
- you click Change password (which writes them as part of the
  rotation flow)

### Recovery actions (advanced)

Under the **Recovery actions (advanced)** disclosure on the
Library page:

- **Recreate watched library** — if you deleted the BookOrbit
  library through its own UI and need biblichor to recreate it.
  Uses stored creds; doesn't touch passwords.

- Stored encrypted in `library.db` (`secrets` table, AES-256-GCM)
- Encrypted under a 32-byte key biblichor auto-generates on first
  use at `data/secrets/secrets.key` (mode 0600)
- If you've already run `biblichor backup-key` (Phase 5c) and have
  an age recovery key at `data/secrets/restore.key`, biblichor
  reuses that as the derivation source for unified trust roots
- Clearing creds removes both the username and password; you'll
  need to re-enter them via the Library page or
  `biblichor bookorbit-setup` to restore Scan / doctor auth
- Backups (`biblichor backup`) include `library.db`, so the
  encrypted secrets travel with the rest of biblichor's state.
  Recovery requires the same `secrets.key` (or `restore.key`),
  which `biblichor backup` packages alongside the dump.

## Host filesystem requirements

BookOrbit needs UID 1000 ownership on the shared library mount.
See [Configuration → Host filesystem](Configuration#host-filesystem-requirements)
for the `deploy/fix-perms.sh` helper and the explicit warning about
blanket chowns.
