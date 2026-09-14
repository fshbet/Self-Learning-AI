"""Source classes are curated provenance, not an authority threshold (audit P1.8)."""

from knowledge_platform.core.plugins.registry import get_registry
from knowledge_platform.core.quality.provenance import source_class_for


def test_every_power_bi_source_declares_its_class():
    plugin = get_registry().get("powerbi")
    specs = {s.key: s for s in plugin.sources()}
    assert all(s.source_class for s in specs.values()), [k for k, s in specs.items() if not s.source_class]
    assert {s.source_class for s in specs.values()} <= {"official", "external", "community", "organization"}
    # the vendor's documentation is official; expert third parties are external even at high authority
    assert specs["ms-learn-dax"].source_class == "official"
    assert specs["dax-guide"].source_class == "external" and specs["dax-guide"].authority >= 80
    assert specs["sqlbi-articles"].source_class == "external"


def test_authority_never_implies_official():
    assert source_class_for(95) == "external" and source_class_for(95, "user") == "external"
    assert source_class_for(30, "discovered") == "community"
