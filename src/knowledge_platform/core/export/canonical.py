"""Canonical serialisation and integrity hashing (req. 5, 30, 39).

Rules that make snapshots reproducible:
* UTF-8, sorted keys, compact separators, ``ensure_ascii=False``;
* records ordered by id; timestamps ISO-8601 in UTC; volatile fields (``updated_at``, embeddings) excluded;
* per-file SHA-256 and an integrity hash over the sorted ``path:sha256`` lines (the manifest is excluded
  because it carries the hash).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def dumps_canonical(obj: Any) -> str:
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def jsonl(records: Iterable[BaseModel | dict[str, Any]]) -> bytes:
    lines = [dumps_canonical(r) for r in records]
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def integrity_hash(files: dict[str, str]) -> str:
    """``files`` maps path -> sha256 (with prefix). Manifest must not be included."""
    lines = sorted(f"{path}:{digest}" for path, digest in files.items() if path != "manifest.json")
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
