# tests/unit/test_phase6w1_http_foundation.py
def test_curl_cffi_imports():
    from curl_cffi import requests as cffi_requests
    assert cffi_requests.Session is not None

import hashlib

def test_solve_anubis_finds_valid_nonce_at_difficulty_8():
    from endless_library.scrapers.anubis import solve_anubis
    challenge = "abc"
    n = solve_anubis(challenge, 8)
    h = hashlib.sha256(f"{challenge}{n}".encode()).digest()
    assert h[0] == 0   # 8 leading zero bits


def test_solve_anubis_handles_non_byte_aligned_difficulty():
    from endless_library.scrapers.anubis import solve_anubis
    challenge = "test"
    n = solve_anubis(challenge, 12)
    h = hashlib.sha256(f"{challenge}{n}".encode()).digest()
    assert h[0] == 0
    assert (h[1] & 0xF0) == 0   # next 4 bits also zero


def test_solve_anubis_returns_int():
    from endless_library.scrapers.anubis import solve_anubis
    n = solve_anubis("x", 4)
    assert isinstance(n, int) and n >= 0


def test_solve_anubis_zero_difficulty_returns_zero():
    from endless_library.scrapers.anubis import solve_anubis
    assert solve_anubis("any", 0) == 0
