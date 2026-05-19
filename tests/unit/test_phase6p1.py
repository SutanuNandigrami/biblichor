"""Phase 6p.1 tests for the encrypted secrets store."""

from __future__ import annotations

import sqlite3

import pytest
from cryptography.exceptions import InvalidTag

from endless_library.secrets_store import (
    SecretsError,
    delete_secret,
    derive_secrets_key,
    get_secret,
    init_secrets_table,
    list_secret_names,
    rotate_secrets,
    set_secret,
)


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(":memory:")
    init_secrets_table(c)
    yield c
    c.close()


@pytest.fixture
def restore_key_file(tmp_path):
    """A fake age-keygen-shaped file. derive_secrets_key only reads
    bytes — it doesn't care about the format."""
    p = tmp_path / "restore.key"
    p.write_bytes(b"# created: 2026-05-20\n# public key: age1xyz\nAGE-SECRET-KEY-1ABC\n")
    return p


# ============ derive_secrets_key ============


def test_derive_returns_32_bytes(restore_key_file):
    k = derive_secrets_key(restore_key_file)
    assert isinstance(k, bytes)
    assert len(k) == 32


def test_derive_is_deterministic(restore_key_file):
    """Same input file -> same key. Otherwise we couldn't decrypt
    anything after a restart."""
    k1 = derive_secrets_key(restore_key_file)
    k2 = derive_secrets_key(restore_key_file)
    assert k1 == k2


def test_derive_differs_for_different_files(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.write_bytes(b"AGE-SECRET-KEY-1AAA")
    b.write_bytes(b"AGE-SECRET-KEY-1BBB")
    assert derive_secrets_key(a) != derive_secrets_key(b)


def test_derive_missing_file_raises(tmp_path):
    with pytest.raises(SecretsError, match="recovery key not found"):
        derive_secrets_key(tmp_path / "does-not-exist.key")


# ============ set / get round-trip ============


def test_round_trip(conn, restore_key_file):
    k = derive_secrets_key(restore_key_file)
    set_secret(conn, k, "bookorbit.admin_password", "Password1!")
    assert get_secret(conn, k, "bookorbit.admin_password") == "Password1!"


def test_get_missing_returns_none(conn, restore_key_file):
    k = derive_secrets_key(restore_key_file)
    assert get_secret(conn, k, "never-set") is None


def test_set_overwrites_existing(conn, restore_key_file):
    k = derive_secrets_key(restore_key_file)
    set_secret(conn, k, "x", "first")
    set_secret(conn, k, "x", "second")
    assert get_secret(conn, k, "x") == "second"


def test_unicode_value_survives_round_trip(conn, restore_key_file):
    """Bengali book metadata could include unicode in passwords/usernames."""
    k = derive_secrets_key(restore_key_file)
    set_secret(conn, k, "user.note", "এই বইটি পড়ুন")
    assert get_secret(conn, k, "user.note") == "এই বইটি পড়ুন"


# ============ tamper / wrong-key detection ============


def test_wrong_key_raises_invalid_tag(conn, restore_key_file, tmp_path):
    """Decrypting with the wrong key fails — silent corruption would
    be worse than a loud error."""
    k = derive_secrets_key(restore_key_file)
    set_secret(conn, k, "x", "value")

    other = tmp_path / "other.key"
    other.write_bytes(b"DIFFERENT-AGE-KEY-FILE")
    wrong = derive_secrets_key(other)

    with pytest.raises(InvalidTag):
        get_secret(conn, wrong, "x")


def test_tampered_ciphertext_raises(conn, restore_key_file):
    """GCM authentication tag catches a flipped bit."""
    k = derive_secrets_key(restore_key_file)
    set_secret(conn, k, "x", "value")
    # Flip a bit in the stored ciphertext
    row = conn.execute("SELECT ciphertext FROM secrets WHERE name='x'").fetchone()
    bad_ct = bytes([row[0][0] ^ 0x01]) + row[0][1:]
    conn.execute("UPDATE secrets SET ciphertext = ? WHERE name = 'x'", (bad_ct,))

    with pytest.raises(InvalidTag):
        get_secret(conn, k, "x")


def test_name_in_aad_prevents_swap_attack(conn, restore_key_file):
    """The secret name is authenticated as AAD. Swapping rows would
    let an attacker rename `bookorbit.password` to `admin.password`
    silently — AAD binding prevents that."""
    k = derive_secrets_key(restore_key_file)
    set_secret(conn, k, "a", "value-a")
    set_secret(conn, k, "b", "value-b")
    # Take a's nonce+ciphertext and store it under b
    row_a = conn.execute("SELECT nonce, ciphertext FROM secrets WHERE name='a'").fetchone()
    conn.execute(
        "UPDATE secrets SET nonce = ?, ciphertext = ? WHERE name = 'b'",
        (row_a[0], row_a[1]),
    )
    with pytest.raises(InvalidTag):
        get_secret(conn, k, "b")


# ============ delete / list ============


def test_delete_removes_secret(conn, restore_key_file):
    k = derive_secrets_key(restore_key_file)
    set_secret(conn, k, "x", "v")
    delete_secret(conn, "x")
    assert get_secret(conn, k, "x") is None


def test_list_returns_sorted_names(conn, restore_key_file):
    k = derive_secrets_key(restore_key_file)
    for name in ("c", "a", "b"):
        set_secret(conn, k, name, "v")
    assert list_secret_names(conn) == ["a", "b", "c"]


# ============ rotation ============


def test_rotation_re_encrypts_all_secrets(conn, restore_key_file, tmp_path):
    k_old = derive_secrets_key(restore_key_file)
    set_secret(conn, k_old, "x", "value-x")
    set_secret(conn, k_old, "y", "value-y")

    new_key_file = tmp_path / "new.key"
    new_key_file.write_bytes(b"NEW-AGE-KEY-DIFFERENT-FROM-OLD")
    k_new = derive_secrets_key(new_key_file)

    count = rotate_secrets(conn, k_old, k_new)
    assert count == 2

    # Old key no longer works
    with pytest.raises(InvalidTag):
        get_secret(conn, k_old, "x")
    # New key does
    assert get_secret(conn, k_new, "x") == "value-x"
    assert get_secret(conn, k_new, "y") == "value-y"


def test_rotation_rolls_back_on_failure(conn, restore_key_file, tmp_path):
    """If decryption of any secret fails mid-rotation, no secret is
    re-encrypted (atomic). Otherwise we'd end up with a mix of
    old-key and new-key ciphertexts and no way to know which is which."""
    k_old = derive_secrets_key(restore_key_file)
    set_secret(conn, k_old, "good", "v")
    # Corrupt a row so the rotation will fail partway
    set_secret(conn, k_old, "bad", "v")
    conn.execute("UPDATE secrets SET ciphertext = X'00' WHERE name = 'bad'")
    conn.commit()

    new_key_file = tmp_path / "new.key"
    new_key_file.write_bytes(b"NEW")
    k_new = derive_secrets_key(new_key_file)

    with pytest.raises(InvalidTag):
        rotate_secrets(conn, k_old, k_new)

    # The 'good' secret is still decryptable with the OLD key,
    # because we rolled back.
    assert get_secret(conn, k_old, "good") == "v"


# ============ key length validation ============


def test_set_rejects_wrong_key_length(conn):
    with pytest.raises(SecretsError, match="32 bytes"):
        set_secret(conn, b"short", "x", "v")


def test_get_rejects_wrong_key_length(conn):
    with pytest.raises(SecretsError, match="32 bytes"):
        get_secret(conn, b"short", "x")
