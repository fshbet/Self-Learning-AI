import json

from knowledge_platform.core.export.canonical import dumps_canonical, integrity_hash, jsonl, sha256_bytes
from knowledge_platform.core.export.render import markdown_to_html
from knowledge_platform.core.export.schema import KnowledgeRecord
from knowledge_platform.core.quality.provenance import derive_polarity, derive_provenance


class _Src:
    def __init__(self, origin, authority, source_class=None):
        self.origin, self.authority, self.source_class = origin, authority, source_class


def test_canonical_json_is_stable_and_sorted():
    a = dumps_canonical({"b": 1, "a": [3, {"z": 1, "y": 2}]})
    b = dumps_canonical({"a": [3, {"y": 2, "z": 1}], "b": 1})
    assert a == b == '{"a":[3,{"y":2,"z":1}],"b":1}'
    lines = jsonl([{"k": 1}, {"k": 2}]).decode()
    assert lines == '{"k":1}\n{"k":2}\n'
    assert sha256_bytes(b"x").startswith("sha256:")


def test_integrity_hash_excludes_manifest_and_is_order_independent():
    files = {"b.jsonl": "sha256:2", "a.jsonl": "sha256:1"}
    h1 = integrity_hash(files)
    h2 = integrity_hash({"a.jsonl": "sha256:1", "b.jsonl": "sha256:2", "manifest.json": "sha256:ignored"})
    assert h1 == h2
    assert integrity_hash({"a.jsonl": "sha256:changed", "b.jsonl": "sha256:2"}) != h1


def test_provenance_rules():
    # provenance follows the *declared* class of the most authoritative source; authority never implies official
    assert derive_provenance([_Src("plugin", 95, "official")]) == "OFFICIAL"
    assert derive_provenance([_Src("plugin", 95)]) == "EXTERNAL"  # undeclared: external, however authoritative
    assert derive_provenance([_Src("plugin", 60)]) == "EXTERNAL"
    assert derive_provenance([_Src("discovered", 30)]) == "COMMUNITY"
    # a URL added by a user is still external content unless classified; USER is reserved for authored knowledge
    assert derive_provenance([_Src("user", 70)]) == "EXTERNAL"
    assert derive_provenance([_Src("user", 95)]) == "EXTERNAL"
    assert derive_provenance([_Src("user", 70, "organization")]) == "ORGANIZATION"
    assert derive_provenance([_Src("user", 70), _Src("plugin", 95, "official")]) == "OFFICIAL"
    assert derive_provenance([_Src("plugin", 85, "external"), _Src("plugin", 80, "official")]) == "EXTERNAL"
    assert derive_provenance([]) == "DERIVED"
    assert derive_polarity("limitation") == "negative" and derive_polarity("fact") == "positive"


def test_knowledge_record_schema_validates():
    rec = KnowledgeRecord(
        id="x",
        domain="d",
        knowledge_type="fact",
        subject="S",
        predicate="p",
        object="o",
        statement="S p o",
        status="VERIFIED",
        confidence=0.8,
        verification_level=2,
        first_discovered_at="2026-01-01T00:00:00Z",
        content_hash="sha256:abc",
    )
    assert json.loads(dumps_canonical(rec))["origin"] == "DIRECT"


def test_markdown_to_html_handles_headings_lists_code_and_tables():
    md = "# T\n\n- **a** — b `c`\n\n```\nx = 1\n```\n\n| k | v |\n|---|---|\n| 1 | 2 |\n"
    out = markdown_to_html(md, "t")
    assert "<h1>T</h1>" in out and "<li><strong>a</strong>" in out and "<pre><code>x = 1" in out
    assert "<table>" in out and "<td>1</td>" in out and "<title>t</title>" in out
