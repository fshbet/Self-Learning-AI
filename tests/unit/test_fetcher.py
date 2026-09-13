import httpx
import pytest
import respx

from knowledge_platform.core.collection.fetcher import FetchBlocked, Fetcher

ROBOTS = "User-agent: *\nDisallow: /private/\nCrawl-delay: 0\n"


@respx.mock
def test_robots_disallow_blocks_fetch():
    respx.get("https://site.test/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS))
    respx.get("https://site.test/public").mock(
        return_value=httpx.Response(200, text="<p>ok</p>", headers={"Content-Type": "text/html"})
    )
    f = Fetcher(default_delay=0, max_retries=1)
    assert f.allowed("https://site.test/public")
    assert not f.allowed("https://site.test/private/x")
    with pytest.raises(FetchBlocked):
        f.fetch("https://site.test/private/x")
    assert f.fetch("https://site.test/public").status == 200


@respx.mock
def test_conditional_get_and_retry_after_429():
    respx.get("https://site.test/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get("https://site.test/page")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(304),
    ]
    f = Fetcher(default_delay=0, max_retries=3)
    res = f.fetch("https://site.test/page", etag='"abc"')
    assert res.not_modified
    sent = route.calls[-1].request
    assert sent.headers["If-None-Match"] == '"abc"'
    assert "KnowledgePlatformBot" in sent.headers["User-Agent"]
    assert route.call_count == 2
