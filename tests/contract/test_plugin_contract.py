"""Every domain directory must satisfy the plugin contract (§6, §44)."""

from pathlib import Path

import pytest

from knowledge_platform.core.plugins.base import DomainPlugin, Manifest, Validator
from knowledge_platform.core.plugins.registry import PluginLoadError, load_plugin_dir

DOMAINS_DIR = Path(__file__).resolve().parents[2] / "domains"
DOMAINS = sorted(p for p in DOMAINS_DIR.iterdir() if (p / "plugin.yaml").exists())


@pytest.mark.parametrize("path", DOMAINS, ids=[p.name for p in DOMAINS])
def test_plugin_loads_and_conforms(path: Path):
    plugin = load_plugin_dir(path)
    assert isinstance(plugin, DomainPlugin)
    assert plugin.id == path.name, "directory name must equal plugin id"
    assert plugin.taxonomy_paths(), "taxonomy must not be empty"
    assert plugin.knowledge_types()
    assert "default" in plugin.risk_classes()
    for spec in plugin.sources():
        assert spec.url.startswith("http"), spec.key
        assert 0 <= spec.authority <= 100
        assert set(spec.permissions) >= {"read", "store", "process", "train", "redistribute"}
    for v in plugin.validators():
        assert isinstance(v, Validator) and v.name and v.version
        assert v.applies_to({}) is False  # an empty item must never crash or apply
    for q in plugin.evaluation_set():
        assert q.id and q.question
        assert q.risk_class in plugin.risk_classes()
    assert plugin.summary()["id"] == plugin.id


def test_incompatible_api_version_is_rejected(tmp_path: Path):
    (tmp_path / "plugin.yaml").write_text("api_version: '9.0'\nid: bad\nname: Bad\n", encoding="utf-8")
    with pytest.raises(PluginLoadError):
        load_plugin_dir(tmp_path)


def test_manifest_requires_slug_id():
    with pytest.raises(ValueError):
        Manifest(api_version="1.0", id="not a slug", name="x")


def test_powerbi_validators():
    plugin = load_plugin_dir(DOMAINS_DIR / "powerbi")
    dax = next(v for v in plugin.validators() if v.name == "dax-syntax")
    good = {"code": "Sales YTD := CALCULATE ( [Sales], DATESYTD ( 'Date'[Date] ) )"}
    bad = {"code": "Sales YTD := CALCULATE ( [Sales], DATESYTD ( 'Date'[Date] )"}
    unknown = {"code": "X := CALCULATE ( NOTAFUNCTION ( 1 ) )"}
    assert dax.applies_to(good)
    assert dax.validate(good).passed
    assert not dax.validate(bad).passed
    assert "NOTAFUNCTION" in dax.validate(unknown).details["unknown_functions"]
    m = next(v for v in plugin.validators() if v.name == "m-syntax")
    assert m.applies_to({"code": "let Source = 1 in Source"})
    assert m.validate({"code": "let Source = 1 in Source"}).passed
    assert not m.validate({"code": "let Source = 1"}).passed
