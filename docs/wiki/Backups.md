# Backups and recovery

biblichor's state lives in:

- `data/library.db` — sqlite (book queue, candidates, events,
  sources, mirrors, bench history, encrypted credentials)
- `library/` — the BookOrbit-watched library (the actual book
  files)
- `data/bookorbit-db/` — BookOrbit's Postgres data (reading
  progress, sync state, user accounts)
- `data/secrets/` — recovery key + secrets key
- `config/config.yaml` and `config/.env` + `<repo>/.env` — settings
  and secrets

`biblichor backup` packages these into a single encrypted
`.tar.zst.age` bundle.

## Creating a backup

```bash
# Full backup including BookOrbit's Postgres dump
biblichor backup \
    --postgres-container biblichor-bookorbit-db \
    --bookorbit-data ./data/bookorbit

# Minimal backup (no Postgres, no library files)
biblichor backup --no-bookorbit-data --no-postgres
```

Output goes to `data/backups/biblichor-backup-<timestamp>.tar.zst.age`.
Encrypted under your **age recovery key**; without it, the bundle
is unrecoverable.

Schedule daily with cron:

```cron
0 3 * * * cd /home/<you>/biblichor && \
    .venv/bin/python -m endless_library backup \
    --postgres-container biblichor-bookorbit-db \
    --bookorbit-data ./data/bookorbit
```

(On compose installs, use `docker compose exec -T biblichor ...`.)

## Restoring

### sqlite + library files

```bash
biblichor restore data/backups/biblichor-backup-<timestamp>.tar.zst.age
```

`restore` decrypts, untars, and dry-runs the schema before
overwriting. Run while biblichor is stopped:

```bash
docker compose -f deploy/compose.yml --env-file .env stop biblichor
biblichor restore ...
docker compose -f deploy/compose.yml --env-file .env start biblichor
```

### Postgres

For a separate Postgres restore (e.g. after a fresh BookOrbit
install on a new host):

```bash
biblichor restore-postgres data/backups/bookorbit-pg-<timestamp>.sql.zst \
    --container biblichor-bookorbit-db
```

## The recovery key

`biblichor backup-key` manages an age keypair at
`data/secrets/restore.key` (mode 0600). The public key encrypts;
the private key decrypts. The private key is the ONLY way to open
encrypted backups.

```bash
biblichor backup-key                    # show / generate
biblichor backup-key --self-mail        # email a copy to yourself
biblichor backup-key --rotate           # rotate (re-encrypts old bundles
                                        # NOT — only new bundles use the new key)
```

### Where to keep the recovery key

In order of strength:

1. **Password manager** (1Password, Bitwarden) — your existing
   secret rotation rhythm covers it
2. **Self-mailed copy in your own inbox** —
   `biblichor backup-key --self-mail`
3. **Printed and stored physically**

Lose the recovery key → every encrypted bundle becomes opaque.
biblichor will not (cannot) help you recover them.

## Encrypted secrets store

Stored credentials (BookOrbit admin user + password) live in
`library.db` under AES-256-GCM, encrypted with a 32-byte symmetric
key.

### Where the symmetric key lives

In order of preference:

1. **Derived from the age recovery key** at
   `data/secrets/restore.key` (if you've run `biblichor backup-key`).
   Both backups and secrets share one trust root.
2. **Dedicated `data/secrets/secrets.key`** (mode 0600). biblichor
   auto-generates this on first use if the age key isn't there.
   Backups and secrets are independent.

Either way, the key never leaves `data/secrets/`.

### Recovery scenarios

| Scenario | Outcome |
|---|---|
| Same host, sqlite corruption | `biblichor restore` brings library.db back; the key on disk is unchanged; secrets decrypt fine |
| Cross-host migration | Copy BOTH `library.db` AND `data/secrets/` to the new host. `biblichor backup` already bundles them together. |
| Lost key, have library.db | Encrypted secrets are unrecoverable (by design). SPA shows a "Re-enter credentials" wizard; you type them in once, biblichor encrypts under a fresh key |

### Rotation

`biblichor backup-key --rotate` rotates the age key. When a
rotation runs and there are stored secrets, biblichor re-encrypts
them atomically: decrypt-old + encrypt-new in one transaction,
rolled back on any decrypt failure. So you never end up with a mix
of old-key and new-key ciphertexts.

## Disaster recovery checklist

Recovering on a fresh host:

1. Install biblichor (compose or native — see [Installation](Installation))
2. Stop the stack
3. Restore your latest `biblichor-backup-*.tar.zst.age` via
   `biblichor restore`
4. Restore Postgres via `biblichor restore-postgres`
5. Run `biblichor bookorbit-doctor` — all 6 checks should green
   (it may flag `auth.creds_provided` if you didn't migrate
   `data/secrets/`; re-enter via the Library page wizard)
6. Start the stack
7. Verify the queue page populates with your old history
