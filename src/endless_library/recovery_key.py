"""Recovery key management for biblichor backups.

The age private key is the ONLY way to decrypt an encrypted backup.
If the user loses it, they lose access to every encrypted bundle.
biblichor never reuploads it — it stays on the user's local disk.

This module:
  - generates an age keypair via `age-keygen` if `data/secrets/restore.key`
    doesn't exist yet,
  - mode-600s the file so other local users can't read it,
  - returns the public key (used as the encryption recipient) +
    the file path so backup.py can hand it to age,
  - prints a one-time STORE THIS SAFELY banner.

Optional: self-mail the private key via Gmail so the user has an
offsite copy in their own inbox. Disabled by default.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class RecoveryKeyError(Exception):
    pass


@dataclass
class RecoveryKey:
    private_key_path: Path
    public_key: str
    just_generated: bool


def ensure_recovery_key(secrets_dir: Path) -> RecoveryKey:
    """Return the recovery keypair, generating it on first call.

    secrets_dir: typically `data/secrets/`. Created if missing.
    The private key file is `restore.key`, mode 600. The public key
    is parsed out of the same file (age-keygen embeds both, with the
    public key on a `# public key:` comment line).
    """
    secrets_dir = Path(secrets_dir)
    secrets_dir.mkdir(parents=True, exist_ok=True)
    key_path = secrets_dir / "restore.key"

    if key_path.exists():
        return RecoveryKey(
            private_key_path=key_path,
            public_key=_extract_public(key_path),
            just_generated=False,
        )

    if shutil.which("age-keygen") is None:
        raise RecoveryKeyError("age-keygen not on PATH — install age via apt/brew")

    proc = subprocess.run(
        ["age-keygen", "-o", str(key_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RecoveryKeyError(f"age-keygen failed: {(proc.stderr or proc.stdout)[-400:]}")
    # Lock down the file so other local users can't read the private key.
    os.chmod(key_path, 0o600)
    pubkey = _extract_public(key_path)
    log.info("generated new recovery key at %s (public: %s)", key_path, pubkey)
    return RecoveryKey(
        private_key_path=key_path,
        public_key=pubkey,
        just_generated=True,
    )


def _extract_public(key_path: Path) -> str:
    """age-keygen output has the form:
    # created: ...
    # public key: age1...
    AGE-SECRET-KEY-1...
    """
    for line in key_path.read_text().splitlines():
        if line.startswith("# public key:"):
            return line.split(":", 1)[1].strip()
    raise RecoveryKeyError(f"could not find public key line in {key_path}")


STORE_THIS_BANNER = """
{bar}
{bar}
   RECOVERY KEY GENERATED — STORE THIS SAFELY

   Path on disk:  {path}
   Public key:    {pub}

   The private key in that file is the ONLY way to decrypt
   biblichor encrypted backups. If you lose it, you lose
   access to every encrypted bundle you create from now on.

   Recommended actions:
     1. Copy {path} to a password manager (1Password / Bitwarden / etc).
     2. Email a copy to yourself: biblichor backup-key --self-mail
     3. Print it.

{bar}
{bar}
""".strip()


def print_warning(rk: RecoveryKey) -> None:
    bar = "=" * 60
    print(STORE_THIS_BANNER.format(bar=bar, path=rk.private_key_path, pub=rk.public_key))
