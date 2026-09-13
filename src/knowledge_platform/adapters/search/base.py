"""Web search interface used by discovery."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    engine: str = ""


class SearchProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def search(self, query: str, *, limit: int = 10) -> list[SearchHit]: ...

    def healthy(self) -> bool:
        """Cheap liveness probe; must return quickly because /api/health calls it."""
        return False
