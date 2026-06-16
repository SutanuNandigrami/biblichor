"""Short-TTL cache of resolved partner-CDN URLs, keyed by md5.

When the same md5 is asked for twice in quick succession -- biblichor
retrying a transient download failure, a queue-replay after a brief
network blip, the bench harness exercising `--repeat`, etc. -- the
first resolve walked the full patchright + DDG dance to extract a
partner URL like `https://b4mcx2ml.net/d3/abc~xyz`. The second
resolve doesn't need to do any of that: hand back the same URL.

Annas signs partner URLs with a per-session token in the `~/...`
segment. The signature is good for a while but not forever; once
the partner CDN starts 4xx-ing the URL we MUST invalidate so the
next resolve walks the full path again. Default TTL is 1 hour; on
the first 4xx the caller should call `invalidate(md5)`.

File-backed for persistence across restarts. Falls back to in-memory
only if the path is unwritable (tests / sandboxed envs).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Annas's signed `/d3/` URLs have lasted hours in practice, but the
# signature TTL isn't documented. 1 hour stays comfortably under the
# bound and limits the blast radius if annas ever shortens it.
DEFAULT_TTL_SECONDS = 3600


@dataclass(slots=True)
class PartnerURLEntry:
    url: str
    expires_at: float
    issued_at: float = field(default_factory=time.time)


class PartnerURLCache:
    """File-backed cache of partner-CDN URLs keyed by md5.

    Construct with `path=None` for tests / environments without a
    writable storage location; the cache then stays in memory only.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._mem: dict[str, PartnerURLEntry] = {}
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
        for md5, payload in raw.items():
            try:
                self._mem[md5] = PartnerURLEntry(
                    url=str(payload["url"]),
                    expires_at=float(payload["expires_at"]),
                    issued_at=float(payload.get("issued_at", payload["expires_at"])),
                )
            except (KeyError, TypeError, ValueError):
                continue

    def get(self, md5: str) -> PartnerURLEntry | None:
        self._ensure_loaded()
        entry = self._mem.get(md5)
        if entry is None:
            return None
        if entry.expires_at <= time.time():
            return None
        return entry

    def set(
        self,
        md5: str,
        url: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._ensure_loaded()
        now = time.time()
        self._mem[md5] = PartnerURLEntry(
            url=url,
            expires_at=now + ttl_seconds,
            issued_at=now,
        )
        self._flush()

    def invalidate(self, md5: str) -> None:
        self._ensure_loaded()
        if md5 in self._mem:
            del self._mem[md5]
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
            log.warning("PartnerURLCache flush failed (%s); staying in-memory", e)
