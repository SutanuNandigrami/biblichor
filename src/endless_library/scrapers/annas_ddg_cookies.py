"""DDoS-Guard cookie cache for Anna's Archive scraping.

DDG sets a small set of `__ddg*` cookies after a client passes the JS
challenge. Those cookies are valid for hours and let plain HTTP clients
walk through the slow_download flow without re-running Chromium. We
capture them once at the end of a patchright resolve and replay them
via httpx until they expire (or get rejected with 403 / fresh DDG
interstitial).

Persisted to a small JSON file so cookies survive container restarts.
Falls back to in-memory only if the path is unwritable.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# DDG cookies live for several hours in practice. 30 min keeps us
# comfortably under the natural TTL and limits the blast radius if
# annas ever rotates its DDG instance mid-shift.
DEFAULT_TTL_SECONDS = 1800


@dataclass(slots=True)
class DDGCookieEntry:
    cookies: dict[str, str]
    user_agent: str
    expires_at: float
    issued_at: float = field(default_factory=time.time)


class DDGCookieCache:
    """File-backed cache of DDG cookie jars keyed by mirror base URL.

    Construct with `path=None` for tests / environments without a
    writable storage location; the cache then stays in memory only.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._mem: dict[str, DDGCookieEntry] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.path is None:
            return
        try:
            raw = json.loads(self.path.read_text())
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        for mirror, payload in raw.items():
            try:
                self._mem[mirror] = DDGCookieEntry(
                    cookies=dict(payload["cookies"]),
                    user_agent=str(payload["user_agent"]),
                    expires_at=float(payload["expires_at"]),
                    issued_at=float(payload.get("issued_at", payload["expires_at"])),
                )
            except (KeyError, TypeError, ValueError):
                continue

    def get(self, mirror: str) -> DDGCookieEntry | None:
        self._ensure_loaded()
        entry = self._mem.get(mirror)
        if entry is None:
            return None
        if entry.expires_at <= time.time():
            return None
        return entry

    def set(
        self,
        mirror: str,
        cookies: dict[str, str],
        user_agent: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._ensure_loaded()
        now = time.time()
        self._mem[mirror] = DDGCookieEntry(
            cookies=dict(cookies),
            user_agent=user_agent,
            expires_at=now + ttl_seconds,
            issued_at=now,
        )
        self._flush()

    def invalidate(self, mirror: str) -> None:
        self._ensure_loaded()
        if mirror in self._mem:
            del self._mem[mirror]
            self._flush()

    def _flush(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps({m: asdict(e) for m, e in self._mem.items()}))
            tmp.replace(self.path)
        except OSError as e:
            log.warning("DDGCookieCache flush failed (%s); staying in-memory", e)
