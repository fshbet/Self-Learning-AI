"""Domain plugin contract (§6 of the design document).

A domain is a directory containing:

    plugin.yaml       manifest: taxonomy, terminology, knowledge types, risk classes
    sources.yaml      seed source catalog with authority and collection rules
    evaluation.yaml   golden questions (optional)
    plugin.py         optional Python: validators and skills

The core never calls domain code outside the ``DomainPlugin`` interface, and
plugins never touch the database — they receive and return plain objects.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from ... import PLUGIN_API_VERSION

# ----------------------------------------------------------------------------- manifest


class TaxonomyNode(BaseModel):
    name: str
    description: str = ""
    children: list[TaxonomyNode] = Field(default_factory=list)

    @field_validator("children", mode="before")
    @classmethod
    def _coerce_children(cls, v: Any) -> Any:
        # allow shorthand: children: ["A", "B"]
        if isinstance(v, list):
            return [{"name": c} if isinstance(c, str) else c for c in v]
        return v


class RiskClass(BaseModel):
    description: str = ""
    min_verification_level: int = 1


class Manifest(BaseModel):
    api_version: str
    id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    language: str = "en"
    taxonomy: list[TaxonomyNode] = Field(default_factory=list)
    terminology: dict[str, str] = Field(default_factory=dict)
    knowledge_types: list[str] = Field(
        default_factory=lambda: ["fact", "definition", "procedure", "example", "best_practice", "limitation"]
    )
    risk_classes: dict[str, RiskClass] = Field(default_factory=lambda: {"default": RiskClass()})
    extraction_hints: str = ""
    discovery_queries: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not v or not all(c.isalnum() or c in "-_" for c in v):
            raise ValueError("plugin id must be a slug (letters, digits, '-', '_')")
        return v

    @field_validator("api_version")
    @classmethod
    def _compatible(cls, v: str) -> str:
        if str(v).split(".")[0] != PLUGIN_API_VERSION.split(".")[0]:
            raise ValueError(f"plugin targets api_version {v}, core implements {PLUGIN_API_VERSION}")
        return str(v)


class SourceSpec(BaseModel):
    key: str
    name: str
    url: str
    publisher: str = ""
    source_type: str = "web"
    authority: int = 50
    source_class: str = ""  # official | external | community | organization (derived from authority if empty)
    relevance: int = 50
    access_type: str = "public"
    license: str = ""
    permissions: dict[str, bool] = Field(
        default_factory=lambda: {"read": True, "store": True, "process": True, "train": False, "redistribute": False}
    )
    crawl_frequency_hours: int = 168
    max_depth: int = 2
    max_pages: int = 50
    allow_patterns: list[str] = Field(default_factory=list)
    deny_patterns: list[str] = Field(default_factory=list)
    enabled: bool = True
    notes: str = ""


class EvalQuestion(BaseModel):
    id: str
    question: str
    expected_answer: str = ""
    acceptable_alternatives: list[str] = Field(default_factory=list)
    required_concepts: list[str] = Field(default_factory=list)
    authoritative_sources: list[str] = Field(default_factory=list)
    difficulty: str = "medium"
    risk_class: str = "default"
    topic: str = ""
    # evaluation semantics (req. 15)
    expect_abstain: bool = False  # the correct behaviour is to say the knowledge base does not cover it
    expected_version: str = ""  # a product version the answer must mention
    must_not_contain: list[str] = Field(default_factory=list)
    negative: bool = False  # asks about a limitation / what does not work


# ----------------------------------------------------------------------------- validators / skills


@dataclass
class ValidationResult:
    validator: str
    version: str
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)
    message: str = ""


class Validator(ABC):
    """A domain validator tests a knowledge item rather than judging it (§20).

    ``applies_to`` decides cheaply whether the validator is relevant; ``validate``
    must be side-effect free. Validators that execute code must do so in a
    sandbox — the core will not enforce that for you.
    """

    name: str = "abstract"
    version: str = "0.0.0"

    def applies_to(self, item: dict[str, Any]) -> bool:
        return False

    def validate(self, item: dict[str, Any]) -> ValidationResult:  # pragma: no cover - abstract
        raise NotImplementedError


@dataclass
class Skill:
    name: str
    description: str
    system_prompt: str


# ----------------------------------------------------------------------------- plugin


class DomainPlugin:
    """Base class for domains. YAML-only domains use this class directly."""

    def __init__(self, path: Path, manifest: Manifest) -> None:
        self.path = Path(path)
        self.manifest = manifest

    # identity -------------------------------------------------------------
    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def name(self) -> str:
        return self.manifest.name

    # contract -------------------------------------------------------------
    def taxonomy(self) -> list[TaxonomyNode]:
        return self.manifest.taxonomy

    def taxonomy_paths(self) -> list[str]:
        """Flattened 'Parent/Child' paths used by the extractor and the UI."""
        out: list[str] = []

        def walk(nodes: list[TaxonomyNode], prefix: str) -> None:
            for n in nodes:
                p = f"{prefix}/{n.name}" if prefix else n.name
                out.append(p)
                walk(n.children, p)

        walk(self.manifest.taxonomy, "")
        return out

    def terminology(self) -> dict[str, str]:
        return self.manifest.terminology

    def knowledge_types(self) -> list[str]:
        return self.manifest.knowledge_types

    def risk_classes(self) -> dict[str, RiskClass]:
        return self.manifest.risk_classes

    def extraction_hints(self) -> str:
        return self.manifest.extraction_hints

    def discovery_queries(self) -> list[str]:
        return self.manifest.discovery_queries

    def sources(self) -> list[SourceSpec]:
        f = self.path / "sources.yaml"
        if not f.exists():
            return []
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        return [SourceSpec.model_validate(s) for s in data.get("sources", [])]

    def evaluation_set(self) -> list[EvalQuestion]:
        f = self.path / "evaluation.yaml"
        if not f.exists():
            return []
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        return [EvalQuestion.model_validate(q) for q in data.get("questions", [])]

    def evaluation_version(self) -> str:
        """Version of the golden dataset; results are always compared within one dataset version."""
        f = self.path / "evaluation.yaml"
        if not f.exists():
            return ""
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        return str(data.get("version", "0"))

    def validators(self) -> list[Validator]:
        return []

    def skills(self) -> list[Skill]:
        return []

    def export_extensions(self, ctx: dict[str, Any]) -> dict[str, bytes | str]:
        """Extra files for the Canonical Knowledge Snapshot (req. 26), placed under ``ext/``.

        ``ctx`` carries ``snapshot_id``, ``version`` and the gathered ``data`` (canonical records). The core
        writes and hashes whatever is returned; the plugin never touches storage itself.
        """
        return {}

    # convenience ------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.manifest.version,
            "description": self.manifest.description,
            "path": str(self.path),
            "taxonomy_paths": self.taxonomy_paths(),
            "knowledge_types": self.knowledge_types(),
            "terminology_count": len(self.terminology()),
            "sources_count": len(self.sources()),
            "validators": [v.name for v in self.validators()],
            "skills": [s.name for s in self.skills()],
            "evaluation_questions": len(self.evaluation_set()),
            "risk_classes": {k: v.model_dump() for k, v in self.risk_classes().items()},
        }
