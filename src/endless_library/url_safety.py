"""URL safety helpers — block SSRF against link-local / loopback /
RFC1918 / metadata endpoints when accepting third-party URLs.

We accept URLs from two surfaces:
  - /api/mirrors POST body (user-typed in dashboard)
  - mirror.url field forwarded to probe_http(...)

Both are exploitable as SSRF if we don't validate: an attacker who can
type a URL (or a malicious mirror entry survives in the DB) could direct
the probe at http://169.254.169.254/latest/meta-data/iam/security-
credentials/, http://localhost:8090/api/..., or any intranet host.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    """Raised when a URL points at a private/loopback/link-local address
    or uses a non-http(s) scheme."""


# Standard private ranges + link-local + loopback + carrier-grade NAT.
_BLOCKED_NETWORKS_V4: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("0.0.0.0/8"),  # unspecified / current network
    ipaddress.IPv4Network("10.0.0.0/8"),  # RFC1918
    ipaddress.IPv4Network("100.64.0.0/10"),  # carrier-grade NAT — also Tailscale!
    ipaddress.IPv4Network("127.0.0.0/8"),  # loopback
    ipaddress.IPv4Network("169.254.0.0/16"),  # link-local incl. cloud metadata
    ipaddress.IPv4Network("172.16.0.0/12"),  # RFC1918
    ipaddress.IPv4Network("192.0.0.0/24"),  # IETF protocol assignments
    ipaddress.IPv4Network("192.168.0.0/16"),  # RFC1918
    ipaddress.IPv4Network("198.18.0.0/15"),  # benchmark testing
    ipaddress.IPv4Network("224.0.0.0/4"),  # multicast
    ipaddress.IPv4Network("240.0.0.0/4"),  # reserved
)
_BLOCKED_NETWORKS_V6: tuple[ipaddress.IPv6Network, ...] = (
    ipaddress.IPv6Network("::1/128"),  # loopback
    ipaddress.IPv6Network("fc00::/7"),  # unique local
    ipaddress.IPv6Network("fe80::/10"),  # link-local
    ipaddress.IPv6Network("::ffff:0:0/96"),  # IPv4-mapped (catches ::ffff:127.0.0.1)
)


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in net for net in _BLOCKED_NETWORKS_V4)
    return any(ip in net for net in _BLOCKED_NETWORKS_V6)


def assert_safe_url(url: str, *, allow_tailscale_carrier_grade: bool = False) -> None:
    """Raise UnsafeUrlError if url is unsafe to fetch as a third-party HTTP
    resource. The pipeline calls this before storing a user-supplied
    mirror URL AND before probing a stored one (so removing the DB row
    isn\'t enough to undo an attacker who got a bad URL in).

    `allow_tailscale_carrier_grade=True` keeps 100.64.0.0/10 reachable
    (used by Tailscale and required only by our internal flaresolverr /
    BookOrbit cross-talk — for which we use service-name DNS inside the docker network).
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise UnsafeUrlError(f"unsupported URL scheme: {scheme!r}")
    host = (parsed.hostname or "").strip()
    if not host:
        raise UnsafeUrlError("URL has no host")

    lower = host.lower()
    if lower in {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}:
        raise UnsafeUrlError(f"loopback hostname: {host}")
    if lower.endswith(".local") or lower.endswith(".internal") or lower.endswith(".lan"):
        raise UnsafeUrlError(f"internal-only TLD: {host}")
    if lower.endswith(".localhost"):
        raise UnsafeUrlError(f"loopback TLD: {host}")

    # Direct IP — check ranges before doing any DNS
    if _is_blocked_ip(host):
        if (
            allow_tailscale_carrier_grade
            and isinstance(ipaddress.ip_address(host), ipaddress.IPv4Address)
            and ipaddress.IPv4Address(host) in ipaddress.IPv4Network("100.64.0.0/10")
        ):
            return
        raise UnsafeUrlError(f"private/loopback/link-local IP: {host}")

    # Resolve and check all returned addresses
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Can\'t resolve — let the actual fetch fail naturally; refusing here
        # would block legitimate offline-ish boxes from saving a URL.
        return
    for info in infos:
        addr = info[4][0]
        if _is_blocked_ip(addr):
            raise UnsafeUrlError(f"host {host} resolves to private/loopback address {addr}")
