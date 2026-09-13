"""Plugin discovery: scan directories for ``plugin.yaml`` and load them."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import yaml

from ...config import get_settings
from .base import DomainPlugin, Manifest

log = logging.getLogger(__name__)


class PluginLoadError(RuntimeError):
    pass


def load_plugin_dir(path: Path) -> DomainPlugin:
    path = Path(path)
    manifest_file = path / "plugin.yaml"
    if not manifest_file.exists():
        raise PluginLoadError(f"{path}: plugin.yaml not found")
    try:
        raw = yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {}
        manifest = Manifest.model_validate(raw)
    except Exception as exc:
        raise PluginLoadError(f"{manifest_file}: {exc}") from exc

    plugin_cls: type[DomainPlugin] = DomainPlugin
    py = path / "plugin.py"
    if py.exists():
        module_name = f"kp_domain_{manifest.id.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, py)
        if spec is None or spec.loader is None:
            raise PluginLoadError(f"{py}: cannot create import spec")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise PluginLoadError(f"{py}: {exc}") from exc
        candidate = getattr(module, "Plugin", None)
        if candidate is None or not issubclass(candidate, DomainPlugin):
            raise PluginLoadError(f"{py}: must define `class Plugin(DomainPlugin)`")
        plugin_cls = candidate

    plugin = plugin_cls(path, manifest)
    # Fail fast on malformed catalogs so problems surface at load, not mid-crawl.
    plugin.sources()
    plugin.evaluation_set()
    return plugin


class PluginRegistry:
    def __init__(self, dirs: list[Path] | None = None) -> None:
        self.dirs = dirs if dirs is not None else get_settings().domain_dirs
        self._plugins: dict[str, DomainPlugin] = {}
        self.errors: dict[str, str] = {}

    def load(self) -> PluginRegistry:
        self._plugins.clear()
        self.errors.clear()
        for base in self.dirs:
            base = Path(base)
            if not base.exists():
                log.warning("domains path does not exist: %s", base)
                continue
            for child in sorted(base.iterdir()):
                if not child.is_dir() or not (child / "plugin.yaml").exists():
                    continue
                try:
                    plugin = load_plugin_dir(child)
                except PluginLoadError as exc:
                    self.errors[child.name] = str(exc)
                    log.error("failed to load plugin %s: %s", child, exc)
                    continue
                if plugin.id in self._plugins:
                    self.errors[child.name] = f"duplicate plugin id {plugin.id!r}"
                    continue
                self._plugins[plugin.id] = plugin
        return self

    def get(self, plugin_id: str) -> DomainPlugin:
        try:
            return self._plugins[plugin_id]
        except KeyError as exc:
            raise KeyError(f"unknown domain {plugin_id!r}; loaded: {sorted(self._plugins)}") from exc

    def all(self) -> list[DomainPlugin]:
        return list(self._plugins.values())

    def __contains__(self, plugin_id: str) -> bool:
        return plugin_id in self._plugins


_registry: PluginRegistry | None = None


def get_registry(reload: bool = False) -> PluginRegistry:
    global _registry
    if _registry is None or reload:
        _registry = PluginRegistry().load()
    return _registry
