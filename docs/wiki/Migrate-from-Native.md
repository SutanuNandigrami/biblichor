# Migrate from a native systemd install

If you've been running biblichor natively under systemd (the legacy
"advanced way" install), here's the cutover to docker compose
without losing data. The whole thing is a controlled ~5-minute
swap.

## 1. Take a backup first

```bash
cd ~/endless-library
.venv/bin/python -m endless_library backup --no-encrypt --library data/books
```

Keep the resulting `data/backups/biblichor-backup-*.tar.zst` (or
`.age` if you didn't use `--no-encrypt`) somewhere safe.

## 2. Rewrite host paths to container-relative paths

In `config.yaml`:

```yaml
# before
general:
  books_dir: /home/<you>/endless-library/data/books

# after
general:
  books_dir: /data/books
```

In the books table:

```bash
sqlite3 data/library.db \
    "UPDATE books SET file_path = REPLACE(file_path,
        '/home/<you>/endless-library/data/books',
        '/data/books')
     WHERE file_path LIKE '/home/<you>/endless-library/data/books/%';"
```

Inspect non-default `books_dir` values — the `REPLACE` is exact-
string and brittle on edge cases.

## 3. Generate the compose `.env`

Carry over your live secrets (Gmail user/password, Kindle email,
Welib cookie, etc.):

```bash
cp deploy/env.example .env
chmod 600 .env
# Edit .env, copy values from config/.env for:
#   GMAIL_USER / GMAIL_APP_PASSWORD
#   KINDLE_EMAIL
#   WELIB_AUTH_COOKIE  (if set)
#   PUSHOVER_*         (if set)
#
# Leave BOOKORBIT_* blank — bootstrap.sh will generate fresh
# secrets for BookOrbit on first compose start. Don't expect to
# carry these over from a native install.
```

## 4. Stop the native service

```bash
sudo systemctl stop biblichor.service
sudo systemctl disable biblichor.service
# Keep the unit file in place for fast rollback.
```

## 5. Start the compose stack

```bash
./deploy/bootstrap.sh
```

Or skip the bootstrap wizard if you've already set `.env` by hand:

```bash
docker compose -f deploy/compose.yml --env-file .env up -d
```

Watch logs:

```bash
docker compose -f deploy/compose.yml --env-file .env logs -f biblichor bookorbit
```

## 6. Verify

After the stack is healthy:

```bash
docker compose -f deploy/compose.yml --env-file .env exec biblichor \
    python -m endless_library bookorbit-doctor
```

All 6 checks should be green. Visit `http://localhost:8090`:

- Queue page shows your migrated books
- Library page either shows the setup wizard (if you haven't yet
  done first-run setup) or the sync surfaces
- Schedule page shows your sources still scheduled

## 7. Rollback (if anything goes wrong)

```bash
docker compose -f deploy/compose.yml --env-file .env down
sudo systemctl enable --now biblichor.service

# Undo config.yaml books_dir + the sqlite REPLACE using the
# backup tar from step 1:
tar -xf data/backups/biblichor-backup-<timestamp>.tar.zst -C /tmp/restore
cp /tmp/restore/library.db data/library.db
cp /tmp/restore/config.yaml config/config.yaml
```

## Cutover gotchas

Real-world snags hit during live migration on a cloud VM:

- **File ownership flip** — native biblichor writes as your host
  user (typically UID 1000); compose biblichor also runs as UID
  1000, so there's usually no flip. But if your host user has a
  different UID, or you have a pre-Phase-6o.5 image still running
  as root, fix with `deploy/fix-perms.sh`. **Do NOT use a blanket
  chown** — it bricks postgres.
- **BOOKORBIT_* secrets are NOT migrated** — they're generated
  fresh by `bootstrap.sh`. Don't waste time trying to carry them
  over.
- **The sqlite REPLACE is brittle** on non-default `books_dir`. If
  your books_dir was something like `/data/library/books`, adjust
  the SQL carefully.
- **`BOOKORBIT_URL` lives in 3 places** — `<repo>/.env`,
  `<repo>/config/.env`, `<repo>/config/config.yaml`. The compose
  default `http://bookorbit:3000` (service name) is correct for the
  container. `config/.env` overrides `config.yaml` if set.

## What you can leave behind

After a successful cutover, you can delete:

- The Python venv (`.venv/`)
- `/etc/systemd/system/biblichor.service` (once you're sure
  rollback won't be needed)
- `data/cookies/` (FlareSolverr rebuilds these)
- Any stray `data/calibre-library/` — if you used
  `biblichor migrate-to-bookorbit` to populate the new `library/`,
  the old dir is no longer needed.

Don't delete `data/library.db` or `data/secrets/` — those carry
the queue history and recovery key.
