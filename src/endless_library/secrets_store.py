"""Encrypted secrets store backed by sqlite + AES-256-GCM.

Phase 6p.1. Secrets are encrypted under a 32-byte symmetric key
derived from the existing Phase 5c age recovery key
(`data/secrets/restore.key`) via HKDF-SHA256 with the domain-separation
salt b"biblichor-secrets-v1". This reuses the single trust root the
user already manages for backup recovery (no second key to lose).

API:
  init_secrets_table(conn)        -> ensure schema exists
  set_secret(conn, key_bytes, name, value)
  get_secret(conn, key_bytes, name) -> str | None
  delete_secret(conn, name)
  list_secret_names(conn) -> list[str]
  rotate_secrets(conn, old_key_bytes, new_key_bytes)
  derive_secrets_key(restore_key_path: Path) -> bytes

Recovery: see README "Encrypted credentials" section. Lost key =
unrecoverable ciphertext (by design). Re-entry via the SPA setup
wizard generates fresh ciphertext under whatever key is current.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SECRETS_KEY_INFO = b"biblichor-secrets-v1"
NONCE_LEN = 12


class SecretsError(Exception):
    pass


def derive_secrets_key(restore_key_path: Path) -> bytes:
    """Derive a 32-byte AES-256 key from the age recovery key file.

    The age private key file is the input keying material. HKDF with a
    fixed info string gives us domain separation, so this key is
    distinct from any future key derived from the same file under a
    different info string.
    """
    if not restore_key_path.exists():
        raise SecretsError(
            f"recovery key not found at {restore_key_path} — run `biblichor backup-key` first"
        )
    ikm = restore_key_path.read_bytes()
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=SECRETS_KEY_INFO)
    return hkdf.derive(ikm)


def init_secrets_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS secrets (
            name       TEXT PRIMARY KEY,
            nonce      BLOB NOT NULL,
            ciphertext BLOB NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    conn.commit()


def set_secret(conn: sqlite3.Connection, key_bytes: bytes, name: str, value: str) -> None:
    if len(key_bytes) != 32:
        raise SecretsError("secrets key must be 32 bytes (AES-256)")
    aes = AESGCM(key_bytes)
    nonce = os.urandom(NONCE_LEN)
    ct = aes.encrypt(nonce, value.encode("utf-8"), associated_data=name.encode("utf-8"))
    conn.execute(
        """
        INSERT INTO secrets (name, nonce, ciphertext, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            nonce=excluded.nonce,
            ciphertext=excluded.ciphertext,
            updated_at=excluded.updated_at
        """,
        (name, nonce, ct, int(time.time())),
    )
    conn.commit()


def get_secret(conn: sqlite3.Connection, key_bytes: bytes, name: str) -> str | None:
    if len(key_bytes) != 32:
        raise SecretsError("secrets key must be 32 bytes (AES-256)")
    row = conn.execute(
        "SELECT nonce, ciphertext FROM secrets WHERE name = ?",
        (name,),
    ).fetchone()
    if row is None:
        return None
    nonce, ct = row[0], row[1]
    aes = AESGCM(key_bytes)
    pt = aes.decrypt(nonce, ct, associated_data=name.encode("utf-8"))
    return pt.decode("utf-8")


def delete_secret(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("DELETE FROM secrets WHERE name = ?", (name,))
    conn.commit()


def list_secret_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM secrets ORDER BY name").fetchall()
    return [r[0] for r in rows]


def _set_secret_no_commit(
    conn: sqlite3.Connection, key_bytes: bytes, name: str, value: str
) -> None:
    """Insert/replace a secret without committing — for use inside an open transaction."""
    aes = AESGCM(key_bytes)
    nonce = os.urandom(NONCE_LEN)
    ct = aes.encrypt(nonce, value.encode("utf-8"), associated_data=name.encode("utf-8"))
    conn.execute(
        """
        INSERT INTO secrets (name, nonce, ciphertext, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            nonce=excluded.nonce,
            ciphertext=excluded.ciphertext,
            updated_at=excluded.updated_at
        """,
        (name, nonce, ct, int(time.time())),
    )


def rotate_secrets(conn: sqlite3.Connection, old_key_bytes: bytes, new_key_bytes: bytes) -> int:
    """Decrypt every secret under old_key, re-encrypt under new_key.

    Returns the number of secrets rotated. Atomic: ALL secrets are
    decrypted with the old key first (fail-fast if any ciphertext is
    corrupt or the old key is wrong), then a single transaction
    re-encrypts them all. If decryption of ANY secret fails before the
    transaction, nothing is written — no partial migration.
    """
    names = list_secret_names(conn)
    # Phase 1: decrypt everything with old key first — fail early, before any writes
    plaintext_pairs: list[tuple[str, str]] = []
    for name in names:
        pt = get_secret(conn, old_key_bytes, name)
        if pt is None:
            continue
        plaintext_pairs.append((name, pt))
    # Phase 2: single transaction to re-encrypt all under new key
    try:
        conn.execute("BEGIN")
        for name, pt in plaintext_pairs:
            _set_secret_no_commit(conn, new_key_bytes, name, pt)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(plaintext_pairs)
