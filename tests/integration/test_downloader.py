from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path

import pytest

from endless_library.domain.models import DownloadHandle
from endless_library.download import DownloadError, download


class _FakeResp:
    def __init__(self, status: int, payload: bytes, *, ct: str = "application/epub+zip"):
        self.status_code = status
        self._payload = payload
        self.headers = {"content-type": ct}

    def iter_bytes(self, chunk: int):
        for i in range(0, len(self._payload), chunk):
            yield self._payload[i : i + chunk]

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, resp: _FakeResp):
        self.resp = resp

    @contextmanager
    def stream(self, method: str, url: str):
        yield self.resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_successful_download(tmp_path: Path):
    payload = b"x" * 1024
    expected_md5 = hashlib.md5(payload).hexdigest()
    handle = DownloadHandle(url="https://cdn/book.epub", headers={})
    result = download(
        handle,
        dest_dir=tmp_path,
        fallback_name="fallback.epub",
        client_factory=lambda: _FakeClient(_FakeResp(200, payload)),
    )
    assert result.path.name == "book.epub"
    assert result.size == 1024
    assert result.md5 == expected_md5
    # No leftover .part
    assert not (tmp_path / "book.epub.part").exists()


def test_md5_mismatch_raises_and_cleans_part(tmp_path: Path):
    payload = b"x" * 100
    handle = DownloadHandle(url="https://cdn/book.epub", headers={})
    with pytest.raises(DownloadError):
        download(
            handle,
            dest_dir=tmp_path,
            fallback_name="fallback.epub",
            expected_md5="0" * 32,
            client_factory=lambda: _FakeClient(_FakeResp(200, payload)),
        )
    assert not (tmp_path / "book.epub.part").exists()
    assert not (tmp_path / "book.epub").exists()


def test_html_response_rejected(tmp_path: Path):
    handle = DownloadHandle(url="https://cdn/book.epub", headers={})
    with pytest.raises(DownloadError, match="HTML"):
        download(
            handle,
            dest_dir=tmp_path,
            fallback_name="fallback.epub",
            client_factory=lambda: _FakeClient(_FakeResp(200, b"<html>denied", ct="text/html")),
        )


def test_http_error_raises(tmp_path: Path):
    handle = DownloadHandle(url="https://cdn/book.epub", headers={})
    with pytest.raises(DownloadError, match="HTTP 403"):
        download(
            handle,
            dest_dir=tmp_path,
            fallback_name="fallback.epub",
            client_factory=lambda: _FakeClient(_FakeResp(403, b"")),
        )
