"""Apply a delta to its base snapshot and prove the result is the head snapshot (P2.4).

    base snapshot files  +  delta snapshot files  ─►  reconstructed head files  ─►  compared with the head's hashes

This is the formal integrity test of the delta contract, not a convenience: a delta that cannot rebuild the head
byte for byte is a broken delta. The check runs on *files* only (a stored snapshot, an unzipped directory or a zip),
never on the database, so a consumer can run it without the platform.

What is promised (delta@1.3): every record file — knowledge, negative, examples, evidence, relationships, sources,
documents, conflicts, changelog and the AI Knowledge Source — plus glossary.json and the schema/ contract are
reconstructed exactly (the delta ships every record whose stored form differs, and the files follow a canonical
order, see ORDER_KEYS). The human-readable renderings (ai/index.json, ai/knowledge.md, knowledge.html, README.md)
are derived from those records and are not reconstructed; the report lists them as such.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .canonical import dumps_canonical, sha256_bytes

# canonical order of every record file: the full snapshot writes records in exactly this order
ORDER_KEYS: dict[str, Callable[[dict[str, Any]], tuple[str, ...]]] = {
    "knowledge.jsonl": lambda r: (r["id"],),
    "negative.jsonl": lambda r: (r["id"],),
    "examples.jsonl": lambda r: (r["id"],),
    "ai/knowledge.jsonl": lambda r: (r["id"],),
    "evidence.jsonl": lambda r: (r["knowledge_item_id"], r["id"]),
    "relationships.jsonl": lambda r: (r["id"],),
    "sources.jsonl": lambda r: (r["id"],),
    "documents.jsonl": lambda r: (r["id"],),
    "conflicts.json": lambda r: (r["id"],),
    "changelog.jsonl": lambda r: (r["at"], r["id"]),
}
# which delta.json section lists the removed ids of each file
REMOVED_SECTION: dict[str, str] = {
    "knowledge.jsonl": "knowledge",
    "negative.jsonl": "negative",
    "examples.jsonl": "examples",
    "ai/knowledge.jsonl": "ai",
    "evidence.jsonl": "evidence",
    "relationships.jsonl": "relationships",
    "sources.jsonl": "sources",
    "documents.jsonl": "documents",
}
COPIED_FILES = ("glossary.json",)  # with schema/* and ext/*: shipped by the delta only when they differ
DERIVED_FILES = ("ai/index.json", "ai/knowledge.md", "knowledge.html", "README.md")


class SnapshotFiles:
    """A snapshot as files: ``manifest`` (dict) and ``files`` (path -> bytes, manifest excluded)."""

    def __init__(self, manifest: dict[str, Any], files: dict[str, bytes]) -> None:
        self.manifest = manifest
        self.files = files

    @classmethod
    def from_dir(cls, root: Path) -> SnapshotFiles:
        root = Path(root)
        if root.suffix.lower() == ".zip" and root.is_file():
            return cls.from_zip(root.read_bytes())
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        files = {p: (root / p).read_bytes() for p in manifest.get("files", {}) if (root / p).exists()}
        return cls(manifest, files)

    @classmethod
    def from_zip(cls, data: bytes) -> SnapshotFiles:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            manifest_name = next(n for n in names if n.endswith("manifest.json"))
            root = manifest_name[: -len("manifest.json")]
            manifest = json.loads(zf.read(manifest_name).decode("utf-8"))
            files = {p: zf.read(root + p) for p in manifest.get("files", {}) if root + p in names}
        return cls(manifest, files)

    @classmethod
    def from_stored(cls, snap: Any) -> SnapshotFiles:
        """From a Snapshot row (reads the object store)."""
        from .snapshot import read_file

        manifest = dict(snap.manifest or {})
        return cls(manifest, {p: read_file(snap, p) for p in manifest.get("files", {})})


class ApplyError(ValueError):
    pass


def _records(data: bytes | None, path: str) -> dict[str, dict[str, Any]]:
    if not data:
        return {}
    text = data.decode("utf-8")
    if path.endswith(".json"):
        return {str(r["id"]): r for r in json.loads(text)}
    return {str(json.loads(line)["id"]): json.loads(line) for line in text.splitlines() if line.strip()}


def _serialise(path: str, records: list[dict[str, Any]]) -> bytes:
    if path.endswith(".json"):
        return dumps_canonical(records).encode("utf-8")
    lines = [dumps_canonical(r) for r in records]
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def apply_delta(base: SnapshotFiles, delta: SnapshotFiles) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Reconstruct the head files. Returns (files, report); ``report["ok"]`` is True only when every promised file
    hashes to the value the delta manifest recorded for the head (``head_files``)."""
    dm, bm = delta.manifest, base.manifest
    if dm.get("kind") != "delta":
        raise ApplyError("the second snapshot is not a delta")
    if bm.get("kind", "full") != "full":
        raise ApplyError("the base must be a full snapshot")
    if bm.get("integrity_hash") != dm.get("base_integrity_hash"):
        raise ApplyError(
            f"delta was built against base {dm.get('base_snapshot_id')} (integrity {dm.get('base_integrity_hash')}), "
            f"not this snapshot (integrity {bm.get('integrity_hash')})"
        )
    d = json.loads(delta.files["delta.json"].decode("utf-8"))
    out: dict[str, bytes] = {}
    for path, key in ORDER_KEYS.items():
        base_recs = _records(base.files.get(path), path)
        delta_recs = _records(delta.files.get(path), path)
        section = REMOVED_SECTION.get(path)
        removed = set(d.get(section, {}).get("removed", [])) if section else set()
        merged = {k: v for k, v in base_recs.items() if k not in removed}
        merged.update(delta_recs)
        out[path] = _serialise(path, sorted(merged.values(), key=key))
    copied = sorted(p for p in set(base.files) | set(delta.files) if p.startswith(("schema/", "ext/")))
    for path in list(COPIED_FILES) + copied:
        data = delta.files.get(path, base.files.get(path))
        if data is not None:
            out[path] = data
    head_files: dict[str, str] = dm.get("head_files") or {}
    report_files: dict[str, dict[str, Any]] = {}
    for path, data in sorted(out.items()):
        digest = sha256_bytes(data)
        expected = head_files.get(path)
        report_files[path] = {
            "sha256": digest,
            "head_sha256": expected,
            "matches": None if expected is None else digest == expected,
        }
    missing = sorted(p for p in head_files if p not in out and p not in DERIVED_FILES)
    report = {
        "base_snapshot_id": bm.get("snapshot_id"),
        "delta_snapshot_id": dm.get("snapshot_id"),
        "head_snapshot_id": dm.get("head_snapshot_id"),
        "head_version": dm.get("head_version"),
        "delta_version": dm.get("delta_version"),
        "verified_against_head": bool(head_files),
        "files": report_files,
        "not_reconstructed": [p for p in DERIVED_FILES if p in head_files],
        "missing": missing,  # promised by the head but neither reconstructed nor derived: a contract violation
        "ok": bool(head_files)
        and not missing
        and all(f["matches"] for f in report_files.values() if f["matches"] is not None),
    }
    return out, report


def write_files(files: dict[str, bytes], report: dict[str, Any], out_dir: Path) -> None:
    out_dir = Path(out_dir)
    for path, data in files.items():
        target = out_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (out_dir / "reconstruction.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


__all__ = ["DERIVED_FILES", "ORDER_KEYS", "ApplyError", "SnapshotFiles", "apply_delta", "write_files"]
