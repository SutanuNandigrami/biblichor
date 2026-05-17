from __future__ import annotations

import random
import time
from collections import defaultdict, deque
from urllib.parse import urlparse


class TokenBucket:
    """Per-host token bucket: at most `capacity` requests per `period` seconds."""

    def __init__(self, *, capacity: int, period_seconds: float) -> None:
        self.capacity = capacity
        self.period = period_seconds
        self._times: dict[str, deque[float]] = defaultdict(deque)

    def acquire(self, url: str, *, now: float | None = None) -> float:
        """Returns seconds to sleep before the request is allowed (0 if immediate)."""
        host = urlparse(url).netloc
        bucket = self._times[host]
        t = now if now is not None else time.time()
        # Drop entries older than `period`
        while bucket and (t - bucket[0]) >= self.period:
            bucket.popleft()
        if len(bucket) < self.capacity:
            bucket.append(t)
            return 0.0
        # Need to wait until the oldest entry ages out
        wait = self.period - (t - bucket[0])
        # Add jitter to avoid synchronized retries
        wait += random.uniform(0.1, 1.0)
        return max(0.0, wait)


class MirrorRotator:
    """Holds a list of mirror base URLs; rotates on .next_after_failure()."""

    def __init__(self, mirrors: list[str]) -> None:
        if not mirrors:
            raise ValueError("MirrorRotator needs at least one mirror")
        self._mirrors = [m.rstrip("/") for m in mirrors]
        self._idx = 0

    @property
    def current(self) -> str:
        return self._mirrors[self._idx]

    def next_after_failure(self) -> str:
        self._idx = (self._idx + 1) % len(self._mirrors)
        return self.current
