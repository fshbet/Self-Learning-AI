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
from typing import Any, Literal

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


class KnowledgeTypeSpec(BaseModel):
    """A knowledge type and its *semantics* (audit P1.5). The core never assumes what a type means: the plugin says
    whether it is negative knowledge, whether it is a foundation others depend on, a dependent, a structured example
    or neutral, and how to label it. Strings in ``knowledge_types`` are upgraded with DEFAULT_TYPE_SPECS."""

    name: str
    polarity: Literal["positive", "negative"] = "positive"
    role: Literal["foundation", "dependent", "example", "neutral"] = "neutral"
    label: str = ""  # plural heading in exports ("Limitations"); defaults to the name, title-cased
    prefix: str = ""  # singular prefix for negative items in AI text ("Limitation"); defaults to the label
    description: str = ""

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not v or not all(c.isalnum() or c in "_-" for c in v):
            raise ValueError("knowledge type names are slugs (letters, digits, '_', '-')")
        return v

    def heading(self) -> str:
        return self.label or self.name.replace("_", " ").capitalize() + "s"

    def negative_prefix(self) -> str:
        return self.prefix or (self.label.rstrip("s") if self.label else self.name.replace("_", " ").capitalize())


# semantics of the conventional vocabulary; a plugin may override any of them or add its own types
DEFAULT_TYPE_SPECS: dict[str, dict[str, str]] = {
    "definition": {"role": "foundation", "label": "Definitions"},
    "fact": {"role": "foundation", "label": "Facts"},
    "procedure": {"role": "dependent", "label": "Procedures"},
    "example": {"role": "example", "label": "Examples"},
    "best_practice": {"role": "dependent", "label": "Best practices"},
    "limitation": {"role": "dependent", "polarity": "negative", "label": "Limitations", "prefix": "Limitation"},
    "warning": {"role": "dependent", "polarity": "negative", "label": "Warnings", "prefix": "Warning"},
    "anti_pattern": {"role": "dependent", "polarity": "negative", "label": "Anti-patterns", "prefix": "Anti-pattern"},
    "common_mistake": {
        "role": "dependent",
        "polarity": "negative",
        "label": "Common mistakes",
        "prefix": "Common mistake",
    },
    "pitfall": {"role": "dependent", "polarity": "negative", "label": "Pitfalls", "prefix": "Pitfall"},
}


def coerce_type_specs(values: list[Any]) -> list[KnowledgeTypeSpec]:
    out: list[KnowledgeTypeSpec] = []
    for v in values:
        if isinstance(v, KnowledgeTypeSpec):
            out.append(v)
        elif isinstance(v, str):
            out.append(KnowledgeTypeSpec(name=v, **DEFAULT_TYPE_SPECS.get(v, {})))
        elif isinstance(v, dict):
            base = dict(DEFAULT_TYPE_SPECS.get(str(v.get("name", "")), {}))
            base.update(v)
            out.append(KnowledgeTypeSpec.model_validate(base))
        else:
            raise ValueError(f"knowledge type entries must be names or objects, got {type(v).__name__}")
    return out


class RiskClass(BaseModel):
    description: str = ""
    min_verification_level: int = 1


# language tag -> PostgreSQL text-search configuration (stemming / stop words for lexical retrieval). Anything
# else — including multilingual corpora and scripts PostgreSQL has no stemmer for — uses "simple" (plain
# tokenisation, no stemming), which is always correct if less forgiving; vector retrieval is language-neutral.
LANGUAGE_TEXT_SEARCH_CONFIGS: dict[str, str] = {
    "ar": "arabic",
    "da": "danish",
    "de": "german",
    "el": "greek",
    "en": "english",
    "es": "spanish",
    "fi": "finnish",
    "fr": "french",
    "hu": "hungarian",
    "id": "indonesian",
    "it": "italian",
    "nl": "dutch",
    "no": "norwegian",
    "pt": "portuguese",
    "ro": "romanian",
    "ru": "russian",
    "sv": "swedish",
    "tr": "turkish",
}


class DiscoverySpec(BaseModel):
    """How new sources are looked for (ADR 0005). Candidates are scored, filtered and left for approval."""

    queries: list[str] = Field(default_factory=list)  # search queries; `discovery_queries` is the legacy alias
    prefer_hosts: list[str] = Field(default_factory=list)  # publishers the plugin author expects (+25 relevance)
    deny_hosts: list[str] = Field(default_factory=list)  # never registered
    min_relevance: int = Field(default=20, ge=0, le=100)  # below this a hit is not even registered
    max_candidates: int = Field(default=25, ge=1, le=500)  # per discovery run


class RetrievalSpec(BaseModel):
    """How the domain wants to be searched (P2.5). ``text_search_config`` names a PostgreSQL text-search
    configuration explicitly (e.g. ``german``, ``simple``); when absent it follows the manifest language."""

    text_search_config: str | None = None


class Manifest(BaseModel):
    api_version: str
    id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    language: str = "en"
    taxonomy: list[TaxonomyNode] = Field(default_factory=list)
    terminology: dict[str, str] = Field(default_factory=dict)
    knowledge_types: list[KnowledgeTypeSpec] = Field(
        default_factory=lambda: coerce_type_specs(
            ["fact", "definition", "procedure", "example", "best_practice", "limitation"]
        )
    )
    risk_classes: dict[str, RiskClass] = Field(default_factory=lambda: {"default": RiskClass()})
    extraction_hints: str = ""
    discovery_queries: list[str] = Field(default_factory=list)
    sample_questions: list[str] = Field(default_factory=list)  # shown on the Search & Ask page
    retrieval: RetrievalSpec = Field(default_factory=RetrievalSpec)
    discovery: DiscoverySpec = Field(default_factory=DiscoverySpec)

    @field_validator("knowledge_types", mode="before")
    @classmethod
    def _coerce_types(cls, v: Any) -> Any:
        return coerce_type_specs(list(v or []))

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
    mirror_of: str = ""  # key of the source this one republishes (shared primary): counts once for independence


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

    ``applies_to`` decides cheaply whether the validator is relevant; ``validate`` must be side-effect free.

    ``kind`` declares the isolation the validator needs (ADR 0003):

    * ``static`` — a pure function of the payload: no I/O, no subprocesses, never executes item content. Runs in
      the worker process.
    * ``executing`` — evaluates content (DAX, Python, SQL, simulations). The core refuses to run it in-process; it
      only ever runs through the isolated validator runner, and is skipped (recorded as skipped) until that runner
      exists. Declaring ``static`` for a validator that executes content is a contract violation.
    """

    name: str = "abstract"
    version: str = "0.0.0"
    kind: str = "static"  # static | executing

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
        return [t.name for t in self.manifest.knowledge_types]

    # type semantics (audit P1.5): the core asks, the plugin answers --------------
    def type_specs(self) -> list[KnowledgeTypeSpec]:
        return list(self.manifest.knowledge_types)

    def type_spec(self, name: str) -> KnowledgeTypeSpec:
        for t in self.manifest.knowledge_types:
            if t.name == name:
                return t
        return KnowledgeTypeSpec(name=name or "fact", **DEFAULT_TYPE_SPECS.get(name or "", {}))

    def polarity_of(self, name: str) -> str:
        return self.type_spec(name).polarity

    def role_of(self, name: str) -> str:
        return self.type_spec(name).role

    def types_with_role(self, *roles: str) -> tuple[str, ...]:
        return tuple(t.name for t in self.manifest.knowledge_types if t.role in roles)

    def text_search_config(self) -> str:
        """PostgreSQL text-search configuration for lexical retrieval: declared, else derived from ``language``,
        else ``simple``. The core never assumes English."""
        declared = (self.manifest.retrieval.text_search_config or "").strip().lower()
        if declared:
            return declared
        lang = (self.manifest.language or "").strip().lower().split("-")[0].split("_")[0]
        return LANGUAGE_TEXT_SEARCH_CONFIGS.get(lang, "simple")

    def sample_questions(self) -> list[str]:
        return list(self.manifest.sample_questions)

    def risk_classes(self) -> dict[str, RiskClass]:
        return self.manifest.risk_classes

    def extraction_hints(self) -> str:
        return self.manifest.extraction_hints

    def discovery(self) -> DiscoverySpec:
        """Discovery settings; `discovery_queries` (legacy) and `discovery.queries` are merged."""
        spec = self.manifest.discovery.model_copy()
        merged = list(dict.fromkeys(list(spec.queries) + list(self.manifest.discovery_queries)))
        spec.queries = merged
        return spec

    def discovery_queries(self) -> list[str]:
        return self.discovery().queries

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
            "knowledge_type_specs": [t.model_dump() for t in self.type_specs()],
            "sample_questions": self.sample_questions(),
            "language": self.manifest.language,
            "text_search_config": self.text_search_config(),
            "terminology_count": len(self.terminology()),
            "sources_count": len(self.sources()),
            "validators": [v.name for v in self.validators()],
            "skills": [s.name for s in self.skills()],
            "evaluation_questions": len(self.evaluation_set()),
            "risk_classes": {k: v.model_dump() for k, v in self.risk_classes().items()},
        }
