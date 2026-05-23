"""Test for Phase 6z Fix 2: rotate_secrets is now truly atomic.

C-NEW-1: if any secret's ciphertext is tampered, rotate_secrets must fail
BEFORE touching any row — all secrets remain decryptable with the OLD key.
"""
from __future__ import annotations

import sqlite3

import pytest
from cryptography.exceptions import InvalidTag

from endless_library.secrets_store import (
    derive_secrets_key,
    get_secret,
    init_secrets_table,
    rotate_secrets,
    set_secret,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_secrets_table(c)
    yield c
    c.close()


@pytest.fixture
def old_key(tmp_path):
    p = tmp_path / "old.key"
    p.write_bytes(b"OLD-AGE-SECRET-KEY-FOR-ROTATION-TEST")
    return derive_secrets_key(p)


@pytest.fixture
def new_key(tmp_path):
    p = tmp_path / "new.key"
    p.write_bytes(b"NEW-AGE-SECRET-KEY-FOR-ROTATION-TEST-2")
    return derive_secrets_key(p)


def test_rotate_secrets_atomic_on_decrypt_failure(conn, old_key, new_key):
    """Set 3 secrets under old_key, tamper secret #2's ciphertext,
    attempt rotation — all 3 should still be readable with old_key."""
    set_secret(conn, old_key, "alpha", "value-alpha")
    set_secret(conn, old_key, "beta", "value-beta")
    set_secret(conn, old_key, "gamma", "value-gamma")

    # Tamper the ciphertext for 'beta' — decryption will fail
    conn.execute(
        "UPDATE secrets SET ciphertext = X'DEADBEEF' WHERE name = 'beta'"
    )
    conn.commit()

    # Rotation should raise (InvalidTag on beta)
    with pytest.raises(InvalidTag):
        rotate_secrets(conn, old_key, new_key)

    # All secrets that were intact must still be decryptable with the OLD key
    assert get_secret(conn, old_key, "alpha") == "value-alpha", (
        "alpha should still be decryptable with old key after failed rotation"
    )
    assert get_secret(conn, old_key, "gamma") == "value-gamma", (
        "gamma should still be decryptable with old key after failed rotation"
    )
