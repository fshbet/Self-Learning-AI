"""Outbound URL guard (SSRF protection, V2 security §"Never fetch internal addresses"; hardened in audit P1.10).

Every URL the platform fetches — plugin sources, user-added sources, discovered pages, robots.txt and every
redirect hop — must point at a public address. Loopback, private (RFC 1918), link-local, carrier-grade NAT,
multicast, reserved and unspecified ranges are refused, as are hostnames that are conventionally internal
(``localhost``, ``*.local``, ``*.internal``), non-http(s) schemes and URLs carrying credentials.

Three layers:

* ``check_syntax`` — cheap, no network: scheme, credentials, internal names, and IP literals in *every* spelling
  (``127.1``, ``2130706433``, ``0x7f000001``, ``0177.0.0.1``, ``[::ffff:7f00:1]``, zone ids) canonicalised first.
* ``resolve_validated`` — resolves a hostname, validates every address, caches the verdict briefly (TTL, not
  forever) and hands back the validated addresses.
* ``GuardedTransport`` (fetcher.py) — connects to one of *those* addresses, so the socket can never go where a
  second DNS answer (rebinding) points; Host header and TLS name stay the original hostname.

``KP_FETCH_ALLOW_PRIVATE=true`` disables the guard for closed-network deployments that crawl an intranet on purpose.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from urllib.parse import urlsplit

from ...config import get_settings

INTERNAL_SUFFIXES = (".local", ".internal", ".localdomain", ".lan", ".home", ".corp", ".intranet")
INTERNAL_HOSTS = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback", "0.0.0.0"})
# not covered by ipaddress' is_private in every Python version: carrier-grade NAT (RFC 6598)
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
DNS_TTL_SECONDS = 60.0


class PrivateAddressError(ValueError):
    pass


# ----------------------------------------------------------------------------- address classification


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address) and ip.sixtofour is not None:
        ip = ip.sixtofour
    if isinstance(ip, ipaddress.IPv6Address) and ip.teredo is not None:
        ip = ip.teredo[1]
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


def _parse_part(part: str) -> int | None:
    """One dotted part with inet_aton semantics: 0x.. hex, leading-0 octal, else decimal."""
    p = part.strip().lower()
    if not p:
        return None
    try:
        if p.startswith("0x"):
            return int(p[2:] or "0", 16)
        if len(p) > 1 and p.startswith("0") and p.isdigit():
            return int(p, 8)
        if p.isdigit():
            return int(p, 10)
    except ValueError:
        return None
    return None


def canonical_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address a C resolver would connect to for an IP *literal* in any spelling, or None for a name.

    Handles bracketed IPv6 (with a zone id), dotted-quad IPv4, and the inet_aton forms: a single 32-bit integer
    (decimal / hex / octal), ``a.b`` (b is 24 bits), ``a.b.c`` (c is 16 bits) and per-part hex/octal.
    """
    h = host.strip().lower().rstrip(".")
    if not h:
        return None
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    h = h.split("%", 1)[0]  # zone id
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass
    if ":" in h:
        return None
    parts = h.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    values = [_parse_part(p) for p in parts]
    if any(v is None for v in values):
        return None
    vals = [int(v) for v in values if v is not None]
    try:
        if len(vals) == 1:
            n = vals[0]
        elif len(vals) == 2:
            n = (vals[0] << 24) | vals[1]
        elif len(vals) == 3:
            n = (vals[0] << 24) | (vals[1] << 16) | vals[2]
        else:
            n = (vals[0] << 24) | (vals[1] << 16) | (vals[2] << 8) | vals[3]
        if n < 0 or n > 0xFFFFFFFF or any(v > 0xFFFFFFFF for v in vals):
            return None
        return ipaddress.IPv4Address(n)
    except (ValueError, OverflowError):
        return None


# ----------------------------------------------------------------------------- syntax (no network)


def check_syntax(url: str) -> tuple[bool, str]:
    """Everything that can be decided without DNS: scheme, credentials, internal names, IP literals."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return False, f"unsupported scheme {parts.scheme!r}"
    host = parts.hostname
    if not host:
        return False, "missing host"
    if parts.username or parts.password:
        return False, "credentials in URL are not allowed"
    h = host.strip().lower().rstrip(".")
    if h in INTERNAL_HOSTS or h.endswith(INTERNAL_SUFFIXES):
        return False, f"internal hostname {h!r}"
    if get_settings().fetch_allow_private:
        return True, "private addresses allowed by configuration"
    ip = canonical_ip(h)
    if ip is not None and not _ip_is_public(ip):
        return False, f"{h} is a private/internal address ({ip})"
    return True, "syntax ok"


# ----------------------------------------------------------------------------- resolution with a short-lived cache

_cache: dict[str, tuple[float, list[str], str | None]] = {}
_cache_lock = threading.Lock()


def clear_dns_cache() -> None:
    with _cache_lock:
        _cache.clear()


def resolve_validated(host: str, *, now: float | None = None) -> tuple[list[str], str | None]:
    """(validated addresses, refusal reason). Every address the name resolves to must be public; the verdict is
    cached for DNS_TTL_SECONDS so a name that changes its answer (rebinding) is re-checked soon, never trusted
    forever. Unresolvable names return ([], None): the caller connects by name and the OS fails the fetch —
    nothing internal is reachable that way."""
    h = host.strip().lower().rstrip(".")
    ip = canonical_ip(h)
    if ip is not None:
        return ([str(ip)], None) if _ip_is_public(ip) else ([], f"{h} is a private/internal address ({ip})")
    t = now if now is not None else time.monotonic()
    with _cache_lock:
        hit = _cache.get(h)
        if hit and hit[0] > t:
            return list(hit[1]), hit[2]
    try:
        infos = socket.getaddrinfo(h, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return [], None
    addresses: list[str] = []
    reason: str | None = None
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if not _ip_is_public(addr):
            reason = f"{h} resolves to private/internal address {addr}"
            addresses = []
            break
        if str(addr) not in addresses:
            addresses.append(str(addr))
    # prefer IPv4 first for connection stability, keep the order otherwise
    addresses.sort(key=lambda a: 0 if "." in a else 1)
    with _cache_lock:
        _cache[h] = (t + DNS_TTL_SECONDS, list(addresses), reason)
    return addresses, reason


def resolve_public(host: str) -> tuple[bool, str]:
    """(ok, reason) for a hostname or literal — kept for callers that only need a verdict."""
    h = host.strip().lower().rstrip(".")
    if not h:
        return False, "empty host"
    if h in INTERNAL_HOSTS or h.endswith(INTERNAL_SUFFIXES):
        return False, f"internal hostname {h!r}"
    addresses, reason = resolve_validated(h)
    if reason:
        return False, reason
    return True, "public address" if addresses else "unresolvable (fetch will fail on its own)"


def check_url(url: str) -> tuple[bool, str]:
    """(ok, reason): syntax plus resolution. Honours KP_FETCH_ALLOW_PRIVATE."""
    ok, reason = check_syntax(url)
    if not ok or get_settings().fetch_allow_private:
        return ok, reason
    return resolve_public(urlsplit(url).hostname or "")


def ensure_public(url: str) -> None:
    ok, reason = check_url(url)
    if not ok:
        raise PrivateAddressError(f"refusing to fetch {url}: {reason}")


__all__ = [
    "DNS_TTL_SECONDS",
    "PrivateAddressError",
    "canonical_ip",
    "check_syntax",
    "check_url",
    "clear_dns_cache",
    "ensure_public",
    "resolve_public",
    "resolve_validated",
]
