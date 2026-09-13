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
