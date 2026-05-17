from __future__ import annotations

from typing import Literal

Action = Literal["send_native", "convert", "skip"]

NATIVE = {"epub", "azw3", "mobi", "pdf", "txt"}
CONVERTIBLE = {"djvu", "fb2", "cbz", "cbr", "lit", "rtf", "html", "doc", "docx"}


def decide_format_action(ext: str) -> Action:
    e = (ext or "").strip().lstrip(".").lower()
    if e in NATIVE:
        return "send_native"
    if e in CONVERTIBLE:
        return "convert"
    return "skip"
