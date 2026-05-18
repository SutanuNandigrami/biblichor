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

    Handles the >100MB scan-warning dance:
      1. Hit uc?export=download&id=<ID>
      2. If response is the scan-warning HTML (form with confirm token),
         pull the form action + hidden inputs and rebuild a GET URL.
      3. Pass through whatever cookies Drive set; the caller streams the
         actual download with those cookies.
    """
    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0"}
        ) as client:
            r = client.get(DRIVE_UC, params={"export": "download", "id": drive_id})
    except Exception as e:
        log.warning("drive: uc fetch %s failed: %s", drive_id, e)
        return None

    cookies = {c.name: c.value for c in r.cookies.jar}

    # Common shortcuts: 302 to userusercontent.google.com (direct bytes)
    if r.status_code in (302, 303):
        loc = r.headers.get("Location", "")
        if loc:
            return loc, _cookies_as_headers(cookies)

    # Scan-warning HTML page: parse the form
    ctype = r.headers.get("content-type", "")
    if "text/html" in ctype and "drive.usercontent" in r.text:
        soup = BeautifulSoup(r.text, "lxml")
        form = soup.find("form")
        if form is not None:
            action = form.get("action") or DRIVE_USERCONTENT
            params: dict[str, str] = {}
            for inp in form.find_all("input"):
                name = inp.get("name")
                if name:
                    params[name] = inp.get("value", "")
            # Construct GET URL
            from urllib.parse import urlencode

            url = f"{action}?{urlencode(params)}"
            return url, _cookies_as_headers(cookies)

    # No scan-warning, no redirect — Drive may have served bytes directly on
    # this connection. We can't stream from r here (we want the caller to
    # do the streaming), but we can hand back uc?... which it can retry with
    # the cookies we collected.
    final_url = f"{DRIVE_UC}?export=download&id={drive_id}"
    return final_url, _cookies_as_headers(cookies)


def _cookies_as_headers(cookies: dict[str, str]) -> dict[str, str]:
    if not cookies:
        return {}
    return {"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}
