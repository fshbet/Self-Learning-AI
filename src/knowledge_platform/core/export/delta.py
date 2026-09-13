"""Delta snapshots (req. 6): what changed between two full Canonical Knowledge Snapshots.

A delta is computed from the *stored* canonical files of a base and a head full snapshot, so it is exactly
reproducible and never depends on live database state. ``build_delta_snapshot`` first builds (or takes) the head
full snapshot, then diffs record by record:

    knowledge      added / modified / superseded / removed(rejected or excluded) / status_changes
    relationships  added / removed
    sources        added / modified / removed
    evidence       added / removed
    examples, negative  added / modified / removed
    conflicts      opened / resolved
    changelog      transitions the base snapshot did not carry
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import PLUGIN_API_VERSION, __version__
from ...adapters import get_object_store
from ...models import Snapshot, utcnow
from ..plugins.base import DomainPlugin
from .canonical import dumps_canonical, integrity_hash, iso, sha256_bytes
from .schema import SCHEMA_VERSION
from .snapshot import build_snapshot, latest_ready, read_file

DELTA_VERSION = "delta@1.0"

VOLATILE_KNOWLEDGE_FIELDS = ("quality_factors", "last_verified_at", "confidence")  # change without content change


def _records(snap: Snapshot, path: str) -> dict[str, dict[str, Any]]:
    """id -> record for a jsonl file of a stored snapshot (empty when the file is absent)."""
    if path not in (snap.manifest or {}).get("files", {}):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in read_file(snap, path).decode("utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            out[str(rec["id"])] = rec
    return out


def _json_list(snap: Snapshot, path: str) -> dict[str, dict[str, Any]]:
    if path not in (snap.manifest or {}).get("files", {}):
        return {}
    return {str(r["id"]): r for r in json.loads(read_file(snap, path).decode("utf-8"))}


def _rec_hash(rec: dict[str, Any], ignore: tuple[str, ...] = ()) -> str:
    body = {k: v for k, v in rec.items() if k not in ignore}
    return hashlib.sha256(dumps_canonical(body).encode("utf-8")).hexdigest()


def _diff(
    base: dict[str, dict[str, Any]], head: dict[str, dict[str, Any]], ignore: tuple[str, ...] = ()
) -> dict[str, list[str]]:
    added = sorted(k for k in head if k not in base)
    removed = sorted(k for k in base if k not in head)
    modified = sorted(k for k in head if k in base and _rec_hash(head[k], ignore) != _rec_hash(base[k], ignore))
    return {"added": added, "modified": modified, "removed": removed}


def compute_delta(base: Snapshot, head: Snapshot) -> dict[str, Any]:
    kb, kh = _records(base, "knowledge.jsonl"), _records(head, "knowledge.jsonl")
    kdiff = _diff(kb, kh, ignore=VOLATILE_KNOWLEDGE_FIELDS)
    status_changes = [
        {"id": k, "from": kb[k]["status"], "to": kh[k]["status"]}
        for k in sorted(kh)
        if k in kb and kb[k]["status"] != kh[k]["status"]
    ]
    superseded = sorted(k for k in kh if k in kb and kh[k].get("historical") and not kb[k].get("historical"))
    rescored = sorted(
        k
        for k in kh
        if k in kb
        and k not in kdiff["modified"]
        and (
            kh[k].get("confidence") != kb[k].get("confidence")
            or kh[k].get("verification_level") != kb[k].get("verification_level")
        )
    )
    changed_fields = {
        k: sorted(
            f for f in set(kb[k]) | set(kh[k]) if f not in VOLATILE_KNOWLEDGE_FIELDS and kb[k].get(f) != kh[k].get(f)
        )
        for k in kdiff["modified"]
    }
    cb, ch = _json_list(base, "conflicts.json"), _json_list(head, "conflicts.json")
    opened = sorted(k for k in ch if k not in cb or (cb[k]["status"] != "OPEN" and ch[k]["status"] == "OPEN"))
    resolved = sorted(k for k in ch if k in cb and cb[k]["status"] == "OPEN" and ch[k]["status"] != "OPEN")
    # transitions the base did not carry (by id; a timestamp cut-off would miss same-second entries)
    base_log = _records(base, "changelog.jsonl")
    changelog = sorted(
        (r for k, r in _records(head, "changelog.jsonl").items() if k not in base_log),
        key=lambda r: (str(r.get("at", "")), str(r.get("id", ""))),
    )
    return {
        "delta_version": DELTA_VERSION,
        "base": {"snapshot_id": str(base.id), "version": base.version, "integrity_hash": base.integrity_hash},
        "head": {"snapshot_id": str(head.id), "version": head.version, "integrity_hash": head.integrity_hash},
        "knowledge": {
            "added": kdiff["added"],
            "modified": [k for k in kdiff["modified"] if k not in superseded],
            "superseded": superseded,
            "removed": kdiff["removed"],
            "rescored_only": rescored,
            "status_changes": status_changes,
            "changed_fields": changed_fields,
        },
        "relationships": _diff(_records(base, "relationships.jsonl"), _records(head, "relationships.jsonl")),
        "sources": _diff(
            _records(base, "sources.jsonl"),
            _records(head, "sources.jsonl"),
            ignore=("last_checked_at", "document_count"),
        ),
        "evidence": _diff(_records(base, "evidence.jsonl"), _records(head, "evidence.jsonl")),
        "examples": _diff(_records(base, "examples.jsonl"), _records(head, "examples.jsonl"), ignore=("confidence",)),
        "negative": _diff(
            _records(base, "negative.jsonl"), _records(head, "negative.jsonl"), ignore=VOLATILE_KNOWLEDGE_FIELDS
        ),
        "conflicts": {"opened": opened, "resolved": resolved},
        "changelog_entries": len(changelog),
        "_changelog": changelog,
        "_head_knowledge": kh,
        "_head_conflicts": ch,
        "_base_knowledge": kb,
    }


def _counts(d: dict[str, Any]) -> dict[str, int]:
    k = d["knowledge"]
    return {
        "knowledge_added": len(k["added"]),
        "knowledge_modified": len(k["modified"]),
        "knowledge_superseded": len(k["superseded"]),
        "knowledge_removed": len(k["removed"]),
        "knowledge_rescored_only": len(k["rescored_only"]),
        "status_changes": len(k["status_changes"]),
        "relationships_added": len(d["relationships"]["added"]),
        "relationships_removed": len(d["relationships"]["removed"]),
        "sources_added": len(d["sources"]["added"]),
        "sources_modified": len(d["sources"]["modified"]),
        "sources_removed": len(d["sources"]["removed"]),
        "evidence_added": len(d["evidence"]["added"]),
        "evidence_removed": len(d["evidence"]["removed"]),
        "examples_changed": sum(len(v) for v in d["examples"].values()),
        "negative_changed": sum(len(v) for v in d["negative"].values()),
        "conflicts_opened": len(d["conflicts"]["opened"]),
        "conflicts_resolved": len(d["conflicts"]["resolved"]),
        "changelog_entries": d["changelog_entries"],
    }


def _readme(plugin: DomainPlugin, d: dict[str, Any], counts: dict[str, int]) -> str:
    b, h = d["base"], d["head"]
    lines = [
        f"# {plugin.name} — Delta Snapshot",
        "",
        f"Changes from snapshot v{b['version']} to v{h['version']}.",
        f"Base integrity `{b['integrity_hash']}` · head integrity `{h['integrity_hash']}`.",
        "",
        "Apply on top of the base snapshot: upsert the records in `knowledge.jsonl`, `evidence.jsonl`,",
        "`relationships.jsonl`, `sources.jsonl`, `examples.jsonl`, `negative.jsonl`; drop the ids listed under",
        "`removed` in `delta.json`; treat `superseded` and `status_changes` as lifecycle updates.",
        "`ai/knowledge.jsonl` carries the AI Knowledge Source records for added and modified items only.",
        "",
        "| Change | Count |",
        "|---|---|",
        *(f"| {k.replace('_', ' ')} | {v} |" for k, v in counts.items()),
        "",
        "Full record lists are in `delta.json`. Both referenced snapshots remain the canonical versions;",
        "this delta is derived from their stored files and is reproducible.",
        "",
    ]
    return "\n".join(lines)


def build_delta_snapshot(
    session: Session,
    plugin: DomainPlugin,
    *,
    base_snapshot_id: uuid.UUID | None = None,
    head_snapshot_id: uuid.UUID | None = None,
    created_by: str = "api",
) -> Snapshot:
    """Build a delta. Without ``head_snapshot_id`` a fresh full snapshot is built first and used as head."""
    base = session.get(Snapshot, base_snapshot_id) if base_snapshot_id else latest_ready(session, plugin.id)
    if base is None or base.status != "ready" or base.kind != "full" or base.domain_id != plugin.id:
        raise ValueError("a ready full snapshot of this domain is required as the base")
    if head_snapshot_id:
        head = session.get(Snapshot, head_snapshot_id)
        if head is None or head.status != "ready" or head.kind != "full" or head.domain_id != plugin.id:
            raise ValueError("head must be a ready full snapshot of this domain")
    else:
        head = build_snapshot(session, plugin, created_by=created_by)
        if head.status != "ready":
            return head  # the failed full snapshot row carries the gate reasons
    if head.id == base.id:
        raise ValueError("base and head are the same snapshot")

    version = (
        session.execute(
            select(func.coalesce(func.max(Snapshot.version), 0)).where(Snapshot.domain_id == plugin.id)
        ).scalar_one()
        + 1
    )
    snap = Snapshot(
        domain_id=plugin.id,
        version=version,
        kind="delta",
        base_snapshot_id=base.id,
        status="building",
        created_by=created_by,
    )
    session.add(snap)
    session.flush()
    prefix = f"snapshots/{plugin.id}/{snap.id}"
    try:
        d = compute_delta(base, head)
        counts = _counts(d)
        kh, ch = d.pop("_head_knowledge"), d.pop("_head_conflicts")
        changelog = d.pop("_changelog")
        d.pop("_base_knowledge")
        changed_ids = set(d["knowledge"]["added"]) | set(d["knowledge"]["modified"]) | set(d["knowledge"]["superseded"])
        head_evidence = _records(head, "evidence.jsonl")
        head_ai = _records(head, "ai/knowledge.jsonl")
        files: dict[str, bytes] = {
            "delta.json": dumps_canonical(d).encode("utf-8"),
            "knowledge.jsonl": _jsonl(kh[k] for k in sorted(changed_ids)),
            "removed.jsonl": _jsonl(
                {"id": k, "last_status": d["knowledge"]["removed"] and _records(base, "knowledge.jsonl")[k]["status"]}
                for k in d["knowledge"]["removed"]
            ),
            "evidence.jsonl": _jsonl(head_evidence[k] for k in d["evidence"]["added"]),
            "relationships.jsonl": _jsonl(
                _records(head, "relationships.jsonl")[k] for k in d["relationships"]["added"]
            ),
            "sources.jsonl": _jsonl(
                _records(head, "sources.jsonl")[k]
                for k in sorted(set(d["sources"]["added"]) | set(d["sources"]["modified"]))
            ),
            "examples.jsonl": _jsonl(
                _records(head, "examples.jsonl")[k]
                for k in sorted(set(d["examples"]["added"]) | set(d["examples"]["modified"]))
            ),
            "negative.jsonl": _jsonl(
                _records(head, "negative.jsonl")[k]
                for k in sorted(set(d["negative"]["added"]) | set(d["negative"]["modified"]))
            ),
            "conflicts.json": dumps_canonical(
                [ch[k] for k in sorted(set(d["conflicts"]["opened"]) | set(d["conflicts"]["resolved"]))]
            ).encode("utf-8"),
            "changelog.jsonl": _jsonl(changelog),
            "ai/knowledge.jsonl": _jsonl(head_ai[k] for k in sorted(changed_ids) if k in head_ai),
            "README.md": _readme(plugin, d, counts).encode("utf-8"),
        }
        store = get_object_store()
        digests: dict[str, str] = {}
        sizes: dict[str, int] = {}
        for path, content in files.items():
            store.put(
                f"{prefix}/{path}", content, "application/json" if path.endswith((".json", ".jsonl")) else "text/plain"
            )
            digests[path] = sha256_bytes(content)
            sizes[path] = len(content)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "delta_version": DELTA_VERSION,
            "snapshot_id": str(snap.id),
            "snapshot_version": snap.version,
            "kind": "delta",
            "base_snapshot_id": str(base.id),
            "base_version": base.version,
            "base_integrity_hash": base.integrity_hash,
            "head_snapshot_id": str(head.id),
            "head_version": head.version,
            "head_integrity_hash": head.integrity_hash,
            "domain": plugin.id,
            "plugin_name": plugin.name,
            "plugin_version": plugin.manifest.version,
            "plugin_api_version": PLUGIN_API_VERSION,
            "created_at": iso(snap.created_at),
            "platform_version": __version__,
            "counts": counts,
            "files": {p: {"sha256": digests[p], "bytes": sizes[p]} for p in sorted(files)},
            "integrity_hash": integrity_hash(digests),
        }
        manifest_bytes = dumps_canonical(manifest).encode("utf-8")
        store.put(f"{prefix}/manifest.json", manifest_bytes, "application/json")
        snap.manifest = json.loads(manifest_bytes)
        snap.integrity_hash = manifest["integrity_hash"]
        snap.object_prefix = prefix
        snap.size_bytes = sum(sizes.values()) + len(manifest_bytes)
        snap.status = "ready"
    except Exception as exc:
        snap.status = "failed"
        snap.error = str(exc)[:2000]
    snap.finished_at = utcnow()
    session.flush()
    return snap


def _jsonl(records) -> bytes:
    lines = [dumps_canonical(r) for r in records]
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
