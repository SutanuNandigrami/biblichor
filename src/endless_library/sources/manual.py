from __future__ import annotations

from collections.abc import Iterable

from endless_library.domain.models import BookRef


class ManualEntry:
    """No-op adapter. Manual books are inserted directly via the dashboard."""

    name = "manual"

    def list_to_read(self, *, identifier: str, token: str | None) -> Iterable[BookRef]:
        return ()
