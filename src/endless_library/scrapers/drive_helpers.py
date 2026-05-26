"""Helpers for downloading from public Google Drive.

We need two flows:
  1. Single file:    https://drive.google.com/file/d/<FILE_ID>/view
                  -> https://drive.google.com/uc?export=download&id=<FILE_ID>
                     (Drive injects a scan-warning HTML for files >~100MB;
                      we extract the form action + confirm token and submit
                      that.)
  2. Folder listing: https://drive.google.com/drive/folders/<FOLDER_ID>
                  -> https://drive.google.com/embeddedfolderview?id=<FOLDER_ID>
                     (the embedded view is a clean, scrape-friendly HTML
                      list of {filename, file_id} pairs.)

Hand-rolled to avoid adding the `gdown` dep just for kindlebangla.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

DRIVE_UC = "https://drive.google.com/uc"
DRIVE_USERCONTENT = "https://drive.usercontent.google.com/download"
DRIVE_EMBED = "https://drive.google.com/embeddedfolderview"

_FILE_ID_RE = re.compile(r"/(?:file/d/|drive/folders/)([A-Za-z0-9_-]{15,})")
_QUERY_ID_RE = re.compile(r"[?&]id=([A-Za-z0-9_-]{15,})")


@dataclass(frozen=True, slots=True)
class DriveTarget:
    """What a kindlebangla download URL resolves to."""

    kind: str  # 'file' or 'folder'
    drive_id: str


def parse_drive_url(url: str) -> DriveTarget | None:
    """Extract a (kind, id) pair from any Google Drive URL we expect to see.

    Examples:
      https://drive.google.com/file/d/1NW4-EIDqKY4y0du1QMlE-ln47TPb4I9X/view?...
      https://drive.google.com/drive/folders/1_FWBiEYtmquHvvjr77zUOEtfbeIvmuff?...
      https://drive.google.com/uc?export=download&id=1NW4-EIDqKY4y0du1QMlE-ln47TPb4I9X
    """
    if not url:
        return None
    m = _FILE_ID_RE.search(url)
    if m:
        kind = "file" if "/file/d/" in url else "folder"
        return DriveTarget(kind=kind, drive_id=m.group(1))
    m = _QUERY_ID_RE.search(url)
    if m:
        # uc?id=... is always a single file
        return DriveTarget(kind="file", drive_id=m.group(1))
    return None


@dataclass(frozen=True, slots=True)
class DriveFolderEntry:
    filename: str
    drive_id: str


def list_folder(folder_id: str, *, timeout: float = 15.0) -> list[DriveFolderEntry]:
    """Scrape `embeddedfolderview?id=<FOLDER_ID>` for {filename, file_id} pairs.

    Returns [] on any failure — the caller will fall back to whatever it can.
    """
    url = f"{DRIVE_EMBED}?id={folder_id}"
    try:
        r = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
    except Exception as e:
        log.warning("drive: folder fetch %s failed: %s", folder_id, e)
        return []
    if r.status_code != 200:
        log.warning("drive: folder %s -> %s", folder_id, r.status_code)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    entries: list[DriveFolderEntry] = []
    # Each result is a <div class="flip-entry" id="entry-<FILE_ID>"> containing
    # a <div class="flip-entry-title">name</div> and a child <a> to the file.
    for div in soup.select("div.flip-entry"):
        drive_id: str | None = None
        # Pull from id="entry-<FILE_ID>" first
        ent_id = div.get("id", "")
        if ent_id.startswith("entry-"):
            drive_id = ent_id[len("entry-") :]
        # Or from the child <a href> as fallback
        if not drive_id:
            child = div.select_one('a[href*="/file/d/"]')
            if child:
                m = _FILE_ID_RE.search(child.get("href", ""))
                if m:
                    drive_id = m.group(1)
        title_el = div.select_one(".flip-entry-title")
        name = title_el.get_text(" ", strip=True) if title_el else None
        if drive_id and name:
            entries.append(DriveFolderEntry(filename=name, drive_id=drive_id))
    return entries


def find_in_folder(
    entries: list[DriveFolderEntry], title: str, *, ext: str = ".epub"
) -> DriveFolderEntry | None:
    """Pick the best matching file in a folder listing.

    Strategy: prefer entries whose name ends with `ext` (default .epub),
    then take the one whose filename has the most overlap with the queried
    title — Bengali word overlap is enough since these folders are usually
    small (<10 files) and the filenames literally embed the title.
    """
    matches = [e for e in entries if e.filename.lower().endswith(ext.lower())]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    # Rank by shared word count with title
    title_tokens = {t for t in title.lower().split() if t}

    def score(e: DriveFolderEntry) -> int:
        name_tokens = {t for t in e.filename.lower().split() if t}
        return len(title_tokens & name_tokens)

    return max(matches, key=score)


def resolve_download_url(
    drive_id: str, *, timeout: float = 15.0
) -> tuple[str, dict[str, str]] | None:
    """Return (url, cookies-as-headers) for downloading a Drive file.

    Phase 6u.7d: the old flow stopped after the FIRST 302 from
    drive.google.com/uc → drive.usercontent.google.com/download. For
    files Drive flags for virus-scan (any RAR/ZIP > ~25MB, plus some
    smaller ones at Drive's discretion), the redirect target serves an
    HTML interstitial — not the bytes. We were handing that HTML URL
    straight to the downloader, which correctly refused it as "got
    HTML, not book". Now we follow the redirect once more and, if the
    second hop returns the scan-warning HTML, we parse the form there.

    Three response shapes Drive sends:
      A. uc → 302 → usercontent → 200 application/octet-stream  ✓ stream
      B. uc → 302 → usercontent → 200 text/html (form)          ✓ parse form
      C. uc → 200 text/html (form, embedded confirm token)      ✓ parse form
    """
    cookies: dict[str, str] = {}
    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0"}
        ) as client:
            r = client.get(DRIVE_UC, params={"export": "download", "id": drive_id})
            for c in r.cookies.jar:
                cookies[c.name] = c.value

            if r.status_code in (302, 303):
                loc = r.headers.get("Location", "")
                if not loc:
                    return None
                try:
                    r2 = client.get(loc)
                except Exception as e:
                    log.warning("drive: redirect fetch %s failed: %s", loc, e)
                    return loc, _cookies_as_headers(cookies)
                for c in r2.cookies.jar:
                    cookies[c.name] = c.value

                ct2 = r2.headers.get("content-type", "")
                if "text/html" in ct2 and ("virus" in r2.text.lower() or "scan" in r2.text.lower()):
                    parsed = _parse_scan_warning_form(r2.text)
                    if parsed:
                        return parsed, _cookies_as_headers(cookies)
                # Either real bytes (caller streams) or HTML we don't
                # recognise; return the redirect URL with collected cookies.
                return loc, _cookies_as_headers(cookies)

            # No redirect — the uc endpoint may have served the form directly.
            ctype = r.headers.get("content-type", "")
            if "text/html" in ctype:
                parsed = _parse_scan_warning_form(r.text)
                if parsed:
                    return parsed, _cookies_as_headers(cookies)
    except Exception as e:
        log.warning("drive: uc fetch %s failed: %s", drive_id, e)
        return None

    # Fallback: hand back the uc URL with cookies. Caller streams it.
    final_url = f"{DRIVE_UC}?export=download&id={drive_id}"
    return final_url, _cookies_as_headers(cookies)


def _parse_scan_warning_form(html: str) -> str | None:
    """Return the form-submission GET URL from a Drive virus-scan-warning
    page, or None if the page doesn't look like one."""
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    if form is None:
        return None
    action = form.get("action") or DRIVE_USERCONTENT
    params: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if name:
            params[name] = inp.get("value", "")
    if not params:
        return None
    from urllib.parse import urlencode

    return f"{action}?{urlencode(params)}"


def _cookies_as_headers(cookies: dict[str, str]) -> dict[str, str]:
    if not cookies:
        return {}
    return {"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}
