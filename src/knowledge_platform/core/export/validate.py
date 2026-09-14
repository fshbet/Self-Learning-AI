"""Validate snapshot files against the shipped JSON Schemas (audit P1.1).

Used twice: at build time (a snapshot whose files do not satisfy its own schema is never published) and by
``verify_snapshot`` on the stored files, so a consumer can run the very same check with any JSON Schema
validator against ``schema/*.schema.json``.
"""

from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator

from .schema import SCHEMA_TARGETS, json_schema

MAX_ERRORS_PER_FILE = 5


def _documents(data: bytes, layout: str) -> list[Any]:
    text = data.decode("utf-8")
    if layout == "jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    doc = json.loads(text)
    return list(doc) if layout == "json-array" else [doc]


def validate_files(files: dict[str, bytes], *, schemas: dict[str, dict[str, Any]] | None = None) -> list[str]:
    """Return human-readable problems (empty = every present file satisfies its schema). ``files`` maps snapshot
    path -> bytes; families whose file is absent are skipped. ``schemas`` overrides the generated schemas (e.g. the
    ones stored inside the snapshot being verified)."""
    problems: list[str] = []
    for name, (path, layout) in SCHEMA_TARGETS.items():
        if path not in files:
            continue
        schema = (schemas or {}).get(name) or json_schema(name)
        validator = Draft202012Validator(schema)
        try:
            docs = _documents(files[path], layout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            problems.append(f"{path}: not valid {layout}: {exc}")
            continue
        count = 0
        for index, doc in enumerate(docs):
            for err in validator.iter_errors(doc):
                where = "/".join(str(p) for p in err.absolute_path) or "(root)"
                ident = doc.get("id", index) if isinstance(doc, dict) else index
                problems.append(f"{path}[{ident}] {where}: {err.message[:160]}")
                count += 1
                if count >= MAX_ERRORS_PER_FILE:
                    break
            if count >= MAX_ERRORS_PER_FILE:
                problems.append(f"{path}: more errors omitted")
                break
    return problems


__all__ = ["validate_files"]
