"""Ollama embeddings via /api/embed."""

from __future__ import annotations

import httpx

from .base import EmbeddingProvider


class OllamaEmbeddings(EmbeddingProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str, dimension: int, timeout: float = 120.0) -> None:
        self.model = model
        self.dimension = dimension
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        # Keep batches modest so a single long batch cannot time out.
        for i in range(0, len(texts), 32):
            batch = texts[i : i + 32]
            resp = self._client.post("/api/embed", json={"model": self.model, "input": batch})
            resp.raise_for_status()
            vectors = resp.json()["embeddings"]
            for v in vectors:
                if len(v) != self.dimension:
                    raise ValueError(
                        f"Embedding dimension mismatch: model returned {len(v)}, configured {self.dimension}"
                    )
            out.extend(vectors)
        return out
