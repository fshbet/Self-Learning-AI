"""Provider-agnostic embedding interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    name: str = "abstract"
    model: str = ""
    dimension: int = 0

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in order."""

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    @property
    def identity(self) -> str:
        """Stable identifier stored next to every vector (vectors from different models never mix)."""
        return f"{self.name}:{self.model}:{self.dimension}"
