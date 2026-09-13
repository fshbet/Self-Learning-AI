"""Object store interface for raw evidence (HTML snapshots, PDFs, exports)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class ObjectStore(ABC):
    name: str = "abstract"

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Store bytes under ``key``; return the key."""

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...
