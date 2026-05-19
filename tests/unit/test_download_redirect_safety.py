"""Regression + feature-intact tests for download() per-hop redirect safety.

Audit finding: download() used to set follow_redirects=True on the
httpx.Client. A hostile shadow-library response could 302 us at
http://169.254.169.254/latest/... (cloud metadata IMDSv1), http://
127.0.0.1:8090/api/... (SSRF back into ourselves), or any RFC1918
host on the box. We happily streamed the body to disk and passed
the file to convert/send.

The fix walks the chain ourselves with HEAD, calling assert_safe_url
on every hop's Location before re-issuing.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from endless_library.domain.models import DownloadHandle
from endless_library.download import DownloadError, download


class _FakeResp:
    def __init__(self, status: int, payload: bytes, content_type: str = "application/epub+zip"):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self._payload = payload

    def iter_bytes(self, chunk_size: int = 8192):
        for i in range(0, len(self._payload), chunk_size):
            yield self._payload[i : i + chunk_size]

    def read(self):
        return self._payload[:200]


class _RedirectingClient:
    """Mock httpx.Client that returns redirects when head() is called for
    `redirect_map`'s sources, and 200 otherwise. stream() yields the
    final payload only if URL is in `final_payloads`.

    The redirect chain models a hostile CDN that 302s us at a private
    address. The real safety guard (assert_safe_url) is what catches it.
    """

    def __init__(
        self,
        *,
        redirect_map: dict[str, str],
        final_payloads: dict[str, _FakeResp] | None = None,
    ):
        self.redirect_map = redirect_map
        self.final_payloads = final_payloads or {}
        self.head_calls: list[str] = []
        self.stream_calls: list[str] = []

    def head(self, url: str, follow_redirects: bool = True):
        self.head_calls.append(url)
        if url in self.redirect_map:

            class _R:
                status_code = 302
                headers = {"location": self.redirect_map[url]}  # noqa: RUF012

            return _R()

        class _R:
            status_code = 200
            headers = {}  # noqa: RUF012

        return _R()

    @contextmanager
    def stream(self, method: str, url: str):
        self.stream_calls.append(url)
        resp = self.final_payloads.get(url)
        if resp is None:
            raise AssertionError(f"unexpected stream() for url={url}")
        yield resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ============ REGRESSION: SSRF blocked at the first hop ============


def test_initial_url_must_be_safe(tmp_path: Path):
    """A hostile candidate.detail_url that points DIRECTLY at a private
    address should be refused before any HTTP traffic."""
    handle = DownloadHandle(url="http://127.0.0.1:8090/api/keys", headers={})

    with pytest.raises(DownloadError, match="refused unsafe URL"):
        download(handle, dest_dir=tmp_path, fallback_name="x.epub")


def test_initial_url_must_be_http_scheme(tmp_path: Path):
    handle = DownloadHandle(url="file:///etc/passwd", headers={})

    with pytest.raises(DownloadError, match="refused unsafe URL"):
        download(handle, dest_dir=tmp_path, fallback_name="x.epub")


# ============ REGRESSION: SSRF blocked mid-redirect chain ============


def test_redirect_to_private_address_blocked(tmp_path: Path):
    """The exact attack the audit flagged: shadow-library serves a
    Location: http://169.254.169.254/... (cloud metadata IMDSv1).
    Old code followed it. New code refuses at the hop."""
    handle = DownloadHandle(url="https://annas-archive.gl/d/abc", headers={})

    fake = _RedirectingClient(
        redirect_map={
            "https://annas-archive.gl/d/abc": "http://169.254.169.254/latest/meta-data",
        },
    )

    with pytest.raises(DownloadError, match="refused unsafe redirect target"):
        download(
            handle,
            dest_dir=tmp_path,
            fallback_name="x.epub",
            client_factory=lambda: fake,
        )

    # And we never streamed anything
    assert fake.stream_calls == []


def test_redirect_chain_to_loopback_blocked(tmp_path: Path):
    """Multi-hop chain where the final destination is internal."""
    handle = DownloadHandle(url="https://annas-archive.gl/d/abc", headers={})

    fake = _RedirectingClient(
        redirect_map={
            "https://annas-archive.gl/d/abc": "https://cdn.example.com/redir",
            "https://cdn.example.com/redir": "http://localhost:8090/secret",
        },
    )

    with pytest.raises(DownloadError, match="refused unsafe redirect target"):
        download(
            handle,
            dest_dir=tmp_path,
            fallback_name="x.epub",
            client_factory=lambda: fake,
        )


def test_too_many_redirects_blocked(tmp_path: Path):
    """Infinite redirect loop is bounded at 5 hops."""
    handle = DownloadHandle(url="https://annas-archive.gl/d/0", headers={})

    fake = _RedirectingClient(
        redirect_map={
            f"https://annas-archive.gl/d/{i}": f"https://annas-archive.gl/d/{i + 1}"
            for i in range(10)
        },
    )

    with pytest.raises(DownloadError, match="too many redirects"):
        download(
            handle,
            dest_dir=tmp_path,
            fallback_name="x.epub",
            client_factory=lambda: fake,
        )


# ============ FEATURE-INTACT: safe public redirects still work ============


def test_redirect_to_public_cdn_followed(tmp_path: Path):
    """A normal 302 from search-result page to a CDN URL must still work."""
    payload = b"PK\x03\x04" + b"epub-bytes" * 100  # at least look ZIP-y
    handle = DownloadHandle(url="https://annas-archive.gl/d/abc", headers={})
    fake = _RedirectingClient(
        redirect_map={
            "https://annas-archive.gl/d/abc": "https://cdn.example.com/files/book.epub",
        },
        final_payloads={
            "https://cdn.example.com/files/book.epub": _FakeResp(200, payload),
        },
    )

    result = download(
        handle,
        dest_dir=tmp_path,
        fallback_name="fallback.epub",
        client_factory=lambda: fake,
    )
    assert result.size == len(payload)
    # And we hit BOTH the head() probe (for the 302) and stream() (for the body)
    assert fake.head_calls == [
        "https://annas-archive.gl/d/abc",
        "https://cdn.example.com/files/book.epub",
    ]
    assert fake.stream_calls == ["https://cdn.example.com/files/book.epub"]


def test_no_redirect_no_op(tmp_path: Path):
    """Most downloads have no redirect (Drive-style direct URL).
    The HEAD probe should pass through cleanly."""
    payload = b"x" * 512
    handle = DownloadHandle(url="https://cdn.example.com/book.epub", headers={})
    fake = _RedirectingClient(
        redirect_map={},  # no redirects
        final_payloads={
            "https://cdn.example.com/book.epub": _FakeResp(200, payload),
        },
    )

    result = download(
        handle,
        dest_dir=tmp_path,
        fallback_name="fallback.epub",
        client_factory=lambda: fake,
    )
    assert result.size == 512
    assert fake.head_calls == ["https://cdn.example.com/book.epub"]
    assert fake.stream_calls == ["https://cdn.example.com/book.epub"]


def test_relative_redirect_resolved(tmp_path: Path):
    """A relative Location like /files/book.epub gets resolved against
    the current URL — and STILL has safety asserted on the result."""
    payload = b"hello"
    handle = DownloadHandle(url="https://cdn.example.com/d/abc", headers={})
    fake = _RedirectingClient(
        redirect_map={
            "https://cdn.example.com/d/abc": "/files/book.epub",  # relative
        },
        final_payloads={
            "https://cdn.example.com/files/book.epub": _FakeResp(200, payload),
        },
    )

    result = download(
        handle,
        dest_dir=tmp_path,
        fallback_name="fallback.epub",
        client_factory=lambda: fake,
    )
    assert result.size == 5
    # urljoin resolved the relative path against the current URL
    assert "https://cdn.example.com/files/book.epub" in fake.stream_calls
