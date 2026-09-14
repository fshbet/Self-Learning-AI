"""Responsible HTTP fetcher (§12).

* honours robots.txt (including Crawl-delay) per host
* identifies itself with a descriptive User-Agent
* per-host rate limiting and exponential backoff on 429/5xx and Retry-After
* conditional requests (ETag / If-Modified-Since)
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from ...config import get_settings
from .netguard import check_syntax, resolve_validated

log = logging.getLogger(__name__)


class FetchBlocked(Exception):
    """robots.txt (or another policy) forbids fetching this URL."""


def _guard_request(request: httpx.Request) -> None:
    """Cheap syntax layer on every hop (scheme, credentials, internal names, IP literals in any spelling)."""
    ok, reason = check_syntax(str(request.url))
    if not ok:
        raise FetchBlocked(f"refusing {request.url}: {reason}")


class GuardedTransport(httpx.BaseTransport):
    """Resolve once, validate every address, connect to a validated one (audit P1.10).

    The URL's host is replaced by the validated IP for the connection while the ``Host`` header and the TLS
    server name (``sni_hostname`` extension, also used for certificate verification) keep the original hostname.
    A second DNS answer between check and connect — DNS rebinding — therefore cannot move the socket. Applies to
    every request the client makes: initial fetch, redirects and robots.txt. Unresolvable names pass through by
    name and fail in the OS (nothing internal is reachable that way); IP literals are validated directly.
    """

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host and not get_settings().fetch_allow_private:
            addresses, reason = resolve_validated(host)
            if reason:
                raise FetchBlocked(f"refusing {request.url}: {reason}")
            if addresses and addresses[0] != host.strip("[]"):
                ip = addresses[0]
                pinned_host = f"[{ip}]" if ":" in ip else ip
                url = request.url.copy_with(host=pinned_host)
                headers = request.headers.copy()
                headers["Host"] = request.url.netloc.decode("ascii")
                extensions = dict(request.extensions)
                if request.url.scheme == "https":
                    extensions["sni_hostname"] = host
                pinned = httpx.Request(
                    request.method, url, headers=headers, content=request.content, extensions=extensions
                )
                response = self._inner.handle_request(pinned)
                response.request = request  # keep the logical URL for redirects / logging
                return response
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content: bytes = b""
    content_type: str = ""
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False
    headers: dict[str, str] = field(default_factory=dict)


class _HostPolicy:
    def __init__(self, robots: urllib.robotparser.RobotFileParser | None, delay: float) -> None:
        self.robots = robots
        self.delay = delay
        self.next_allowed_at = 0.0
        self.lock = threading.Lock()


class Fetcher:
    def __init__(
        self,
        *,
        user_agent: str | None = None,
        default_delay: float | None = None,
        timeout: float | None = None,
        max_retries: int = 3,
    ) -> None:
        s = get_settings()
        self.user_agent = user_agent or s.user_agent
        self.default_delay = default_delay if default_delay is not None else s.crawl_default_delay_seconds
        self.max_retries = max_retries
        self._client = httpx.Client(
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5",
            },
            timeout=timeout or s.crawl_timeout_seconds,
            follow_redirects=True,
            event_hooks={"request": [_guard_request]},  # SSRF guard applies to every hop, redirects included
            transport=GuardedTransport(httpx.HTTPTransport()),  # resolve once, connect to the validated address
        )
        self._hosts: dict[str, _HostPolicy] = {}
        self._hosts_lock = threading.Lock()

    # ------------------------------------------------------------------ robots
    def _policy(self, url: str) -> _HostPolicy:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        with self._hosts_lock:
            if host in self._hosts:
                return self._hosts[host]
        rp = urllib.robotparser.RobotFileParser()
        delay = self.default_delay
        try:
            resp = self._client.get(f"{host}/robots.txt")
            if resp.status_code >= 500:
                # Treat server errors as "unknown" -> be conservative and allow with default delay.
                rp = None  # type: ignore[assignment]
            elif resp.status_code >= 400:
                rp.parse([])  # no robots -> everything allowed
            else:
                rp.parse(resp.text.splitlines())
                cd = rp.crawl_delay(self.user_agent) or rp.crawl_delay("*")
                if cd:
                    delay = max(delay, float(cd))
        except httpx.HTTPError as exc:
            log.warning("robots.txt fetch failed for %s: %s", host, exc)
            rp = None  # type: ignore[assignment]
        policy = _HostPolicy(rp, delay)
        with self._hosts_lock:
            self._hosts[host] = policy
        return policy

    def allowed(self, url: str) -> bool:
        policy = self._policy(url)
        if policy.robots is None:
            return True
        return policy.robots.can_fetch(self.user_agent, url) and policy.robots.can_fetch("*", url)

    def robots_summary(self, url: str) -> dict[str, object]:
        policy = self._policy(url)
        return {"crawl_delay": policy.delay, "robots_loaded": policy.robots is not None}

    # ------------------------------------------------------------------ fetch
    def _wait_turn(self, policy: _HostPolicy) -> None:
        with policy.lock:
            now = time.monotonic()
            if policy.next_allowed_at > now:
                time.sleep(policy.next_allowed_at - now)
            policy.next_allowed_at = time.monotonic() + policy.delay

    def fetch(self, url: str, *, etag: str | None = None, last_modified: str | None = None) -> FetchResult:
        if not self.allowed(url):
            raise FetchBlocked(f"robots.txt disallows {url}")
        policy = self._policy(url)
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        backoff = 2.0
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._wait_turn(policy)
            try:
                resp = self._client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                last_exc = exc
                log.warning("fetch error %s (attempt %d): %s", url, attempt, exc)
                time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 304:
                return FetchResult(url=url, final_url=str(resp.url), status=304, not_modified=True)
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else backoff
                log.warning("%s -> %s, backing off %.1fs", url, resp.status_code, wait)
                time.sleep(wait)
                backoff *= 2
                last_exc = httpx.HTTPStatusError(f"{resp.status_code}", request=resp.request, response=resp)
                continue
            return FetchResult(
                url=url,
                final_url=str(resp.url),
                status=resp.status_code,
                content=resp.content,
                content_type=resp.headers.get("Content-Type", ""),
                etag=resp.headers.get("ETag"),
                last_modified=resp.headers.get("Last-Modified"),
                headers=dict(resp.headers),
            )
        assert last_exc is not None
        raise last_exc

    def close(self) -> None:
        self._client.close()
