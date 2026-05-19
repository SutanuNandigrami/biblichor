# Security

biblichor pulls binary content from third-party sources, so the
pipeline applies a strict hygiene gate on every download.

## Archive hygiene

Some sources (notably `kindlebangla.com` for ~30% of its catalog)
deliver the ebook inside a RAR or ZIP wrapper. biblichor unpacks
these, but only after every member passes:

| Check | Blocks |
|---|---|
| Magic byte detection | Files claiming to be archives but aren't (RAR or ZIP only) |
| Compressed size cap (default 200 MB) | Bandwidth / disk hoses |
| Extension whitelist | `.epub`/`.azw3`/`.mobi`/`.pdf`/`.jpg`/`.opf` and a few siblings; everything else (`.exe`, `.sh`, `.lnk`, `.so`, …) is fatal |
| Path traversal | Members containing `..` or starting with `/` are fatal |
| Nested archives | Refused outright (no RAR-in-RAR, ZIP-in-ZIP) |
| Zip-bomb protection | Total uncompressed size capped (default 500 MB) |
| Single ebook contract | Must contain exactly one `.epub`/`.azw3`/`.mobi`/`.pdf` |

These rules are **always on**; nothing in `config.yaml` disables
them. Pipeline marks the book `failed` with the reason recorded in
events.

## Optional ClamAV scan

After hygiene passes, the unpacked ebook is handed to `clamscan` if
it's installed. By default ClamAV is treated as optional — its
absence is a loud warning, not a failure.

```bash
# Install
sudo apt install clamav clamav-daemon
sudo freshclam                                # signature DB
sudo systemctl enable --now clamav-freshclam  # daily auto-update

# Then in config/config.yaml:
security:
  require_clamav: true
```

| Pipeline behavior | `require_clamav=false` | `require_clamav=true` |
|---|---|---|
| ClamAV not installed | ⚠ Warn, allow after hygiene pass | ✗ Fail every archive |
| ClamAV reports clean | ✓ Allow | ✓ Allow |
| ClamAV reports infected | ✗ Fail | ✗ Fail |
| ClamAV errors / times out | ✗ Fail | ✗ Fail |

Bare `.epub` / `.pdf` downloads (the common case) are also scanned
by ClamAV when installed; hygiene only applies to archives.

## Tunables

```yaml
security:
  require_clamav: false        # flip to true after installing clamav
  max_archive_size_mb: 200     # compressed wrapper cap
  max_extracted_size_mb: 500   # total uncompressed cap (zip-bomb guard)
  max_members: 50              # max entries in an archive
```

## Encrypted credentials

BookOrbit admin credentials (when stored via the SPA or
`biblichor bookorbit-setup`) live in `library.db` under AES-256-GCM
encryption.

- 32-byte key from HKDF-SHA256, derived from:
  - the existing age recovery key at `data/secrets/restore.key` if
    `biblichor backup-key` has been run, OR
  - a dedicated `data/secrets/secrets.key` biblichor auto-generates
    on first use (mode 0600)
- Each secret is bound to its name as AAD, so swapping ciphertexts
  between rows fails authentication
- Rotation: `biblichor backup-key --rotate` re-encrypts all secrets
  under a new key atomically (decrypt-old + encrypt-new in one
  transaction, rolled back on any failure)

See [Backups → Encrypted secrets store](Backups#encrypted-secrets-store)
for the recovery model.

## Network exposure

- **Dashboard binding** defaults to `0.0.0.0`. Set
  `TAILSCALE_IP=100.x.y.z` in the systemd Environment line (native)
  or as an env var (compose) to bind to a single Tailscale IP and
  keep the dashboard private.
- BookOrbit ports default to `3000` for the dashboard. If you
  bound biblichor to Tailscale, also restrict BookOrbit accordingly
  in `compose.yml`.
- The `/healthz` endpoint is intentionally unauthenticated for
  Docker healthcheck use.

## Secrets and git

The `.gitignore` excludes:

- `data/` (including `library.db` and downloaded files)
- `*.db`
- `config/config.yaml` (operational, generated)
- `config/.env` (app secrets)
- `.env` (compose secrets)
- `data/secrets/` (recovery keys, encrypted secret key file)

If you fork this repo or commit changes, double-check `git status`
before push.

## AGPL and network use

biblichor is AGPL-3.0. If you run a modified version as a network
service, you must make the source available to its users. Linking
to your fork in the dashboard's footer satisfies that obligation
for personal-host deployments.
