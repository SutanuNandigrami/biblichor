# First-run walkthrough

After `bootstrap.sh` finishes, here's how to get from a green
install to books arriving on your Kindle.

## 1. Open BookOrbit setup wizard

Visit `http://<your-host>:8090/library`. If you opted out of the
BookOrbit setup step during `bootstrap.sh`, biblichor shows a
**"First-run setup needed"** card with a wizard:

- Admin username (default: `admin`)
- Display name
- Email
- Password (8+ chars, mixed case + digit)
- Library root (container path; default `/library`)
- Setup token (auto-minted; matches `BOOKORBIT_SETUP_TOKEN` if set
  in `.env`)

Click **Create admin + library**. biblichor calls BookOrbit's
`/auth/setup`, creates a watched library, stores your credentials
encrypted in `library.db`, and flips `bookorbit.enabled = true` in
`config.yaml`. Refresh — you should now see the sync surfaces (OPDS,
Kobo, KOReader, Statistics).

If you ran `bootstrap.sh` end-to-end (default), this step is done
already — the wizard won't appear.

## 2. Add a reading-list source

Sources page → **Add source**. Three popular options:

- **Goodreads shelf** — identifier `YOUR_USER_ID:to-read`. Your
  user ID is the number in any of your bookshelf URLs:
  `https://www.goodreads.com/review/list/12345678-name?shelf=to-read`
- **Goodreads Listopia** — paste a list URL like
  `https://www.goodreads.com/list/show/112351.Name`
- **Goodreads Series** — paste a series URL like
  `https://www.goodreads.com/series/40395-mistborn`
- **Hardcover** — identifier `me`, paste your Bearer token from
  <https://hardcover.app/account/api>
- **Manual** — add individual books by title + author

Each source has its own polling interval; the default is 60
minutes.

## 3. Verify settings

Settings page — confirm:

- SMTP host / port / user are set
- The Kindle email field shows your `@kindle.com` address
- "Send test" actually delivers a test email to your Kindle. If it
  doesn't, see [Troubleshooting](Troubleshooting#kindle-send-fails).

The page never echoes the SMTP password (set-but-not-shown is the
expected display).

## 4. Kick off the first run

Schedule page → click **Run now** on `poll:<your-source-id>` and
then on `process`. Watch the Queue page:

- Books enter as **new**, then **queued**, then **searching**
- They land in **needs_review** if biblichor couldn't auto-pick a
  candidate (e.g. multiple candidates within `auto_pick_gap` of
  each other) — click the book to see the search trace and pick
  manually
- Otherwise they go **downloaded → converted → sent**

## 5. Watch books land in BookOrbit

After a successful Kindle send, biblichor also drops the file into
BookOrbit's watched library. BookOrbit ingests it via embedded
metadata; you should see it on the BookOrbit dashboard within ~30s
of the send.

Click **Open BookOrbit** on the Library page to verify. If you
don't see new books, click **Run doctor** — the 6 checks tell you
exactly where the chain broke.

## 6. Wire up your e-readers

From `http://<your-host>:8090/library`:

- **OPDS catalog** — copy the URL into any OPDS-capable reader
  (KOReader, Thorium, Moon+ Reader, Marvin, Aldiko). First use
  prompts for credentials; create an OPDS password in BookOrbit's
  Account settings.
- **Kobo sync** — set up auto-push via BookOrbit's
  Settings → Kobo. Each device registers itself with a unique
  token; new books appear over Wi-Fi.
- **KOReader sync** — OPDS Catalog → Add Catalog. Use the OPDS URL
  above; KOReader syncs reading progress two-way through the same
  channel.
- **Reading statistics** — open from the Library page for heatmaps,
  streaks, pages-per-day.

## What to do if something doesn't work

- **No books appearing in BookOrbit after a Kindle send** — click
  Run doctor on the Library page. The 6-check report tells you
  exactly which link is broken (URL, library path, credentials,
  DTO drift).
- **Books stuck in needs_review** — open the book to see candidates
  and pick manually. If you keep landing in needs_review, lower
  `auto_pick_gap` in Settings.
- **Source not polling** — check the Schedule page; the job may be
  paused. Pause/resume from the table.
- **403 on Anna's** — Cloudflare challenge active for your IP;
  confirm FlareSolverr is running (`docker compose ps` should show
  flaresolverr as healthy).
- More: see [Troubleshooting](Troubleshooting).
