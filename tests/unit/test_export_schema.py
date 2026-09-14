"""Formal export contract (audit P1.1 / P1.2): generated JSON Schemas, vocabulary, drift guard, validation."""

from __future__ import annotations

import json
from pathlib import Path

from knowledge_platform.core.export.canonical import dumps_canonical
from knowledge_platform.core.export.schema import (
    EXPORT_SCHEMA_VERSION,
    SCHEMA_FILES,
    SCHEMA_TARGETS,
    AIKnowledgeRecord,
    json_schema,
    schema_files,
    vocabulary,
)
from knowledge_platform.core.export.validate import validate_files

DOCS = Path(__file__).resolve().parents[2] / "docs" / "export-schema"


def test_every_snapshot_file_family_has_a_stamped_schema():
    files = schema_files()
    for name in SCHEMA_FILES:
        s = json_schema(name)
        assert s["$schema"].endswith("2020-12/schema") and s["x-export-schema-version"] == EXPORT_SCHEMA_VERSION
        assert s["x-snapshot-file"] == SCHEMA_TARGETS[name][0] and "properties" in s
        assert f"schema/{name}.schema.json" in files
    v = vocabulary()
    assert v["export_schema_version"] == EXPORT_SCHEMA_VERSION
    assert set(v["usage"]) == {"cite", "caution", "historical"}
    assert set(v["origin"]) == {"DIRECT", "DERIVED", "SYNTHESIZED", "EXPERIMENTALLY_VALIDATED"}
    assert set(v["provenance"]) == {"OFFICIAL", "EXTERNAL", "COMMUNITY", "USER", "ORGANIZATION", "DERIVED"}
    assert {"minor", "major", "independent_of"} <= set(v["versioning_policy"])
    # the AI record schema enumerates usage, so a consumer can rely on the three values
    ai = json_schema("ai_knowledge")
    assert ai["properties"]["usage"]["enum"] == ["cite", "caution", "historical"]


def test_committed_schema_copy_matches_the_generated_contract():
    """`kp export schema` output is committed under docs/export-schema; regenerate it when the models change."""
    generated = {Path(p).name: dumps_canonical(doc) + "\n" for p, doc in schema_files().items()}
    for name, body in generated.items():
        on_disk = (DOCS / name).read_text(encoding="utf-8")
        assert on_disk == body, f"docs/export-schema/{name} is stale: run `kp export schema`"
    assert (DOCS / "CHANGELOG.md").read_text(encoding="utf-8").count(f"## {EXPORT_SCHEMA_VERSION} ") == 1


def _ai(**over):
    base = {
        "id": "k1",
        "domain": "d",
        "type": "fact",
        "polarity": "positive",
        "origin": "DIRECT",
        "provenance": "OFFICIAL",
        "topic": "",
        "subject": "S",
        "text": "S p o.",
        "statement": "S p o.",
        "status": "VERIFIED",
        "historical": False,
        "usage": "cite",
        "confidence": 0.8,
        "verification_level": 2,
    }
    base.update(over)
    return base


def test_validate_files_accepts_conforming_records_and_names_the_broken_field():
    ok = {
        "ai/knowledge.jsonl": (json.dumps(_ai()) + "\n").encode(),
        "conflicts.json": b"[]",
    }
    assert validate_files(ok) == []
    bad = {
        "ai/knowledge.jsonl": (
            json.dumps(_ai(usage="trust me")) + "\n" + json.dumps(_ai(id="k2", confidence="high"))
        ).encode(),
        "conflicts.json": b"{not json",
    }
    problems = validate_files(bad)
    assert any(p.startswith("ai/knowledge.jsonl[k1] usage") for p in problems)
    assert any(p.startswith("ai/knowledge.jsonl[k2] confidence") for p in problems)
    assert any(p.startswith("conflicts.json: not valid") for p in problems)
    # the pydantic model and the JSON schema agree
    assert AIKnowledgeRecord.model_validate(_ai()).usage == "cite"
