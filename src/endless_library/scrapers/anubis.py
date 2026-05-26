"""Pure-Python Anubis PoW solver.

Anubis (https://github.com/TecharoHQ/anubis) is an anti-AI / anti-
scrape PoW challenge increasingly deployed on shadow-library-adjacent
forges in 2025+. Server returns a challenge string and difficulty
(number of required leading zero bits in sha256(challenge+nonce));
client submits nonce; server returns a JWT cookie valid for ~50min.

Pure Python; ~1-50ms typical at difficulty 8-16.
"""

from __future__ import annotations

import hashlib


def solve_anubis(challenge: str, difficulty: int) -> int:
    """Find nonce N such that sha256(challenge+str(N)) has at least
    `difficulty` leading zero BITS. Returns the smallest such nonce."""
    if difficulty <= 0:
        return 0
    target_bytes = difficulty // 8
    target_bits = difficulty % 8
    target_mask = (0xFF << (8 - target_bits)) & 0xFF if target_bits else 0
    nonce = 0
    while True:
        h = hashlib.sha256(f"{challenge}{nonce}".encode()).digest()
        if all(b == 0 for b in h[:target_bytes]):
            if not target_bits or (h[target_bytes] & target_mask) == 0:
                return nonce
        nonce += 1
