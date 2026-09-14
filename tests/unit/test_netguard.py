"""SSRF guard: private/internal targets are refused at registration, at fetch time and on redirects."""

import httpx
import pytest
import respx

from knowledge_platform.core.collection.fetcher import FetchBlocked, Fetcher
from knowledge_platform.core.collection.netguard import check_url, resolve_public


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://127.0.0.1:8010/api/settings",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.3.4/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
        "http://100.64.0.1/",  # carrier-grade NAT
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
        "http://printer.local/",
        "http://db.internal/",
        "ftp://example.com/x",
        "file:///etc/passwd",
        "http://user:pw@example.com/",
    ],
)
def test_private_or_unsupported_urls_are_refused(url):
    ok, reason = check_url(url)
    assert not ok, reason


def test_public_and_unresolvable_urls_pass():
    assert check_url("https://93.184.216.34/")[0]
    assert check_url("https://fixture.test/docs/")[0]  # unresolvable: the fetch itself fails, nothing internal reached
    assert resolve_public("LOCALHOST.") == (False, "internal hostname 'localhost'")


@respx.mock
def test_fetcher_blocks_private_targets_and_redirects_to_them():
    respx.get("https://site.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://site.test/go").mock(return_value=httpx.Response(302, headers={"Location": "http://127.0.0.1/x"}))
    private = respx.get("http://127.0.0.1/x").mock(return_value=httpx.Response(200, text="secret"))
    f = Fetcher(default_delay=0, max_retries=1)
    with pytest.raises(FetchBlocked):
        f.fetch("http://10.1.2.3/")
    with pytest.raises(FetchBlocked):
        f.fetch("https://site.test/go")
    assert not private.called


# ----------------------------------------------------------------------------- audit P1.10 hardening


@pytest.mark.parametrize(
    "literal,expected",
    [
        ("127.1", "127.0.0.1"),
        ("127.0.1", "127.0.0.1"),
        ("2130706433", "127.0.0.1"),
        ("0x7f000001", "127.0.0.1"),
        ("0177.0.0.1", "127.0.0.1"),
        ("0x7f.1", "127.0.0.1"),
        ("0", "0.0.0.0"),
        ("[::ffff:7f00:1]", "::ffff:127.0.0.1"),
        ("[fe80::1%eth0]", "fe80::1"),
        ("8.8.8.8", "8.8.8.8"),
        ("example.test", None),
        ("300.1.1.1", None),
        ("1.2.3.4.5", None),
    ],
)
def test_ipv4_and_ipv6_literals_are_canonicalised(literal, expected):
    from knowledge_platform.core.collection.netguard import canonical_ip

    got = canonical_ip(literal)
    assert (str(got) if got is not None else None) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://127.1/",
        "http://2130706433/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
        "http://0/",
        "http://[fe80::1%25eth0]/",
    ],
)
def test_odd_literal_spellings_are_refused_without_dns(url):
    from knowledge_platform.core.collection.netguard import check_syntax

    ok, reason = check_syntax(url)
    assert not ok and "private/internal" in reason


def test_dns_verdicts_expire_instead_of_being_cached_forever(monkeypatch):
    import socket as _socket

    from knowledge_platform.core.collection import netguard

    netguard.clear_dns_cache()
    answers = {"n": 0}

    def fake_getaddrinfo(host, port, proto=0):
        answers["n"] += 1
        ip = "93.184.216.34" if answers["n"] == 1 else "10.0.0.9"  # the name is re-pointed at a private host
        return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(netguard.socket, "getaddrinfo", fake_getaddrinfo)
    assert netguard.resolve_validated("rebind.test", now=1000.0) == (["93.184.216.34"], None)
    # within the TTL the cached, validated answer is reused (no second lookup)
    assert netguard.resolve_validated("rebind.test", now=1000.0 + 5) == (["93.184.216.34"], None)
    assert answers["n"] == 1
    # after the TTL the name is looked up again and the new private answer is refused
    addresses, reason = netguard.resolve_validated("rebind.test", now=1000.0 + netguard.DNS_TTL_SECONDS + 1)
    assert addresses == [] and "private/internal" in reason
    netguard.clear_dns_cache()


class _Capture(httpx.BaseTransport):
    def __init__(self):
        self.requests = []

    def handle_request(self, request):
        self.requests.append(request)
        return httpx.Response(200, text="ok", request=request)


def test_transport_connects_to_the_validated_address_with_the_original_name(monkeypatch):
    from knowledge_platform.core.collection import fetcher as fetcher_mod
    from knowledge_platform.core.collection.fetcher import GuardedTransport

    monkeypatch.setattr(fetcher_mod, "resolve_validated", lambda host: (["93.184.216.34"], None))
    inner = _Capture()
    client = httpx.Client(transport=GuardedTransport(inner))
    r = client.get("https://public.test/docs/page?x=1")
    assert r.status_code == 200
    sent = inner.requests[0]
    assert sent.url.host == "93.184.216.34" and sent.url.path == "/docs/page" and sent.url.query == b"x=1"
    assert sent.headers["Host"] == "public.test" and sent.extensions["sni_hostname"] == "public.test"
    assert r.request.url.host == "public.test"  # the logical request keeps the name
    # a name that now resolves to a private address is refused at connect time
    monkeypatch.setattr(
        fetcher_mod, "resolve_validated", lambda host: ([], "rebind.test resolves to private/internal address 10.0.0.9")
    )
    with pytest.raises(FetchBlocked):
        client.get("https://rebind.test/")
    # unresolvable names pass through by name (the OS fails the fetch); literals are validated directly
    monkeypatch.setattr(fetcher_mod, "resolve_validated", lambda host: ([], None))
    client.get("https://nowhere.test/")
    assert inner.requests[-1].url.host == "nowhere.test"


@respx.mock
def test_literal_spelling_redirect_is_blocked_by_the_fetcher():
    respx.get("https://site.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://site.test/go").mock(
        return_value=httpx.Response(302, headers={"Location": "http://2130706433/admin"})
    )
    f = Fetcher(default_delay=0, max_retries=1)
    with pytest.raises(FetchBlocked):
        f.fetch("https://site.test/go")
