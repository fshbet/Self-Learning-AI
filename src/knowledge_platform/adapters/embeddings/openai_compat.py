"""OpenAI-compatible embeddings (``/v1/embeddings``)."""

from __future__ import annotations

import httpx

from .base import EmbeddingProvider


class OpenAICompatEmbeddings(EmbeddingProvider):
    name = "openai"

    def __init__(self, base_url: str, api_key: str | None, model: str, dimension: int, timeout: float = 120.0) -> None:
        self.model = model
        self.dimension = dimension
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            batch = texts[i : i + 64]
            resp = self._client.post("/embeddings", json={"model": self.model, "input": batch})
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"{resp.status_code}: {resp.text[:300]}", request=resp.request, response=resp
                )
            rows = sorted(resp.json()["data"], key=lambda r: r.get("index", 0))
            for r in rows:
                v = r["embedding"]
                if len(v) != self.dimension:
                    raise ValueError(
                        f"Embedding dimension mismatch: model returned {len(v)}, configured {self.dimension}"
                    )
                out.append(v)
        return out
