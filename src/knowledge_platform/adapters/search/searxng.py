"""SearXNG adapter (self-hosted meta-search, no API key)."""

from __future__ import annotations

import httpx

from .base import SearchHit, SearchProvider


class SearXNGSearch(SearchProvider):
    name = "searxng"

    def __init__(self, base_url: str, timeout: float = 20.0) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def healthy(self) -> bool:
        try:
            return self._client.get("/healthz", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False

    def search(self, query: str, *, limit: int = 10) -> list[SearchHit]:
        resp = self._client.get("/search", params={"q": query, "format": "json", "language": "en"})
        resp.raise_for_status()
        hits = []
        for r in resp.json().get("results", [])[:limit]:
            hits.append(
                SearchHit(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", ""),
                    engine=",".join(r.get("engines", [])),
                )
            )
        return hits
