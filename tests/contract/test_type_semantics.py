"""Plugin-declared knowledge-type semantics (audit P1.5): a domain with its own vocabulary needs no core change."""

from __future__ import annotations

import pytest

from knowledge_platform.core.export.render import ai_markdown, ai_text, type_heading, type_prefix, type_specs
from knowledge_platform.core.export.schema import KnowledgeRecord, Manifest
from knowledge_platform.core.plugins.base import DEFAULT_TYPE_SPECS, KnowledgeTypeSpec, coerce_type_specs
from knowledge_platform.core.plugins.registry import load_plugin_dir
from knowledge_platform.core.quality.provenance import derive_polarity

ROBOTICS = """api_version: '1.0'
id: robotics
name: Robotics
knowledge_types:
  - {name: spec, role: foundation, label: Specifications}
  - {name: safety_rule, role: dependent, label: Safety rules}
  - {name: hazard, role: dependent, polarity: negative, label: Hazards, prefix: Hazard}
  - {name: demo, role: example, label: Demonstrations}
  - note
sample_questions: ['What is the payload limit of the arm?']
taxonomy:
  - name: Arms
"""


@pytest.fixture
def robotics(tmp_path):
    d = tmp_path / "robotics"
    d.mkdir()
    (d / "plugin.yaml").write_text(ROBOTICS, encoding="utf-8")
    return load_plugin_dir(d)


def test_plugin_declares_polarity_roles_and_labels(robotics):
    assert robotics.knowledge_types() == ["spec", "safety_rule", "hazard", "demo", "note"]
    assert robotics.polarity_of("hazard") == "negative" and robotics.polarity_of("safety_rule") == "positive"
    assert robotics.types_with_role("foundation") == ("spec",)
    assert robotics.types_with_role("dependent", "example") == ("safety_rule", "hazard", "demo")
    assert robotics.role_of("demo") == "example" and robotics.role_of("note") == "neutral"
    assert (
        robotics.type_spec("hazard").heading() == "Hazards"
        and robotics.type_spec("hazard").negative_prefix() == "Hazard"
    )
    assert robotics.sample_questions() == ["What is the payload limit of the arm?"]
    # the core asks the plugin; the conventional vocabulary is only a default for plain names
    assert derive_polarity("hazard", robotics) == "negative" and derive_polarity("hazard") == "positive"
    assert derive_polarity("limitation") == "negative"
    summary = robotics.summary()
    assert summary["knowledge_type_specs"][2]["polarity"] == "negative"


def test_plain_names_keep_the_conventional_semantics():
    specs = coerce_type_specs(["fact", "example", "limitation", {"name": "warning", "label": "Cautions"}])
    assert [t.role for t in specs] == ["foundation", "example", "dependent", "dependent"]
    assert specs[2].polarity == "negative" and specs[3].label == "Cautions" and specs[3].polarity == "negative"
    assert set(DEFAULT_TYPE_SPECS) >= {"fact", "definition", "example", "limitation", "warning", "anti_pattern"}
    with pytest.raises(ValueError):
        KnowledgeTypeSpec(name="bad type")


def _rec(kind, polarity="positive", **kw):
    return KnowledgeRecord(
        id=kw.pop("id", kind),
        domain="robotics",
        knowledge_type=kind,
        polarity=polarity,
        subject="ARM",
        predicate="p",
        object="o",
        statement=f"ARM {kind} statement.",
        status="VERIFIED",
        confidence=0.8,
        verification_level=2,
        first_discovered_at="2026-01-01T00:00:00Z",
        content_hash="sha256:x",
        topic="Arms",
        **kw,
    )


def test_export_renders_headings_and_prefixes_from_the_declaration(robotics):
    glossary = {"knowledge_type_specs": [t.model_dump() for t in robotics.type_specs()], "terminology": {}}
    specs = type_specs(glossary)
    assert type_heading("hazard", specs) == "Hazards" and type_prefix("hazard", specs) == "Hazard"
    assert type_heading("unknown_kind", specs) == "Unknown kinds" and type_prefix("unknown_kind", {}) == "Unknown kind"
    hazard = _rec("hazard", "negative")
    assert ai_text(hazard, [], specs=specs).startswith("Hazard: ARM hazard statement.")
    manifest = Manifest(
        snapshot_id="s",
        snapshot_version=1,
        domain="robotics",
        plugin_name="Robotics",
        plugin_version="0.1.0",
        plugin_api_version="1.0",
        created_at="",
        platform_version="0",
    )
    md = ai_markdown(manifest, [_rec("spec"), hazard, _rec("demo")], {}, [], glossary)
    # sections follow the plugin's declared order and labels
    assert md.index("### Specifications") < md.index("### Hazards") < md.index("### Demonstrations")


def test_text_search_config_follows_the_plugin_not_the_core(tmp_path):
    """P2.5: lexical retrieval configuration is declared by (or derived from) the plugin; the core assumes nothing."""
    from knowledge_platform.core.plugins.base import LANGUAGE_TEXT_SEARCH_CONFIGS

    counter = iter(range(100))

    def plugin(extra: str):
        d = tmp_path / f"p{next(counter)}"
        d.mkdir()
        (d / "plugin.yaml").write_text(f"api_version: '1.0'\nid: {d.name}\nname: X\n{extra}\n", encoding="utf-8")
        return load_plugin_dir(d)

    assert plugin("language: en").text_search_config() == "english"
    assert plugin("language: de-DE").text_search_config() == "german"
    assert plugin("language: ja").text_search_config() == "simple"  # no stemmer: plain tokenisation
    assert plugin("language: mul").text_search_config() == "simple"  # multilingual corpus
    assert plugin("language: en\nretrieval:\n  text_search_config: simple").text_search_config() == "simple"
    assert plugin("language: fr\nretrieval:\n  text_search_config: German").text_search_config() == "german"
    assert plugin("language: en").summary()["text_search_config"] == "english"
    assert "english" in LANGUAGE_TEXT_SEARCH_CONFIGS.values() and "simple" not in LANGUAGE_TEXT_SEARCH_CONFIGS.values()
