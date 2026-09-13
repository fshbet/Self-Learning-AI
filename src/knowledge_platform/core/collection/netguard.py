"""Outbound URL guard (SSRF protection, V2 security §"Never fetch internal addresses").

Every URL the platform fetches — plugin sources, user-added sources, discovered pages and every redirect hop — must
point at a public address. Loopback, private (RFC 1918), link-local, carrier-grade NAT, multicast, reserved and
unspecified ranges are refused, as are hostnames that are conventionally internal (``localhost``, ``*.local``,
``*.internal``). Only http(s) is allowed. ``KP_FETCH_ALLOW_PRIVATE=true`` disables the guard for closed-network
deployments that crawl an intranet on purpose.
"""

from __future__ import annotations

import ipaddress
import socket
from functools import lru_cache
from urllib.parse import urlsplit

from ...config import get_settings

INTERNAL_SUFFIXES = (".local", ".internal", ".localdomain", ".lan", ".home", ".corp", ".intranet")
INTERNAL_HOSTS = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback", "0.0.0.0"})
# not covered by ipaddress' is_private in every Python version: carrier-grade NAT (RFC 6598)
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class PrivateAddressError(ValueError):
    pass


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or getattr(ip, "is_site_local", False)
    ):
        return False
    return not (isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT)


@lru_cache(maxsize=4096)
def resolve_public(host: str) -> tuple[bool, str]:
    """(ok, reason) for a hostname: every resolved address must be public. Unresolvable names are allowed through —
    the fetch itself fails, and nothing internal can be reached that way."""
    h = host.strip().lower().rstrip(".")
    if not h:
        return False, "empty host"
    if h in INTERNAL_HOSTS or h.endswith(INTERNAL_SUFFIXES):
        return False, f"internal hostname {h!r}"
    try:
        ip = ipaddress.ip_address(h.strip("[]"))
    except ValueError:
        ip = None
    if ip is not None:
        return (True, "public address") if _ip_is_public(ip) else (False, f"{h} is a private/internal address")
    try:
        infos = socket.getaddrinfo(h, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return True, "unresolvable (fetch will fail on its own)"
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if not _ip_is_public(addr):
            return False, f"{h} resolves to private/internal address {addr}"
    return True, "public address"


def check_url(url: str) -> tuple[bool, str]:
    """(ok, reason). Honours KP_FETCH_ALLOW_PRIVATE."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return False, f"unsupported scheme {parts.scheme!r}"
    if not parts.hostname:
        return False, "missing host"
    if parts.username or parts.password:
        return False, "credentials in URL are not allowed"
    if get_settings().fetch_allow_private:
        return True, "private addresses allowed by configuration"
    return resolve_public(parts.hostname)


def ensure_public(url: str) -> None:
    ok, reason = check_url(url)
    if not ok:
        raise PrivateAddressError(f"refusing to fetch {url}: {reason}")


__all__ = ["PrivateAddressError", "check_url", "ensure_public", "resolve_public"]
