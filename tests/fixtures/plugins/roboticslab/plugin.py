"""Fixture plugin code: one static validator and one executing validator (which the core must refuse to run)."""

from __future__ import annotations

from typing import Any

from knowledge_platform.core.plugins.base import DomainPlugin, ValidationResult, Validator


class UnitsValidator(Validator):
    """Static: a spec that states a payload must give it in kilograms."""

    name = "units"
    version = "1.0"

    def applies_to(self, item: dict[str, Any]) -> bool:
        return item.get("knowledge_type") == "spec" and "payload" in (item.get("statement") or "").lower()

    def validate(self, item: dict[str, Any]) -> ValidationResult:
        ok = " kg" in (item.get("statement") or "")
        return ValidationResult(validator=self.name, version=self.version, passed=ok, message="kg" if ok else "no kg")


class SimulationValidator(Validator):
    """Executing: would run a motion simulation — never allowed in-process (ADR 0003)."""

    name = "simulate-motion"
    version = "0.1"
    kind = "executing"

    def applies_to(self, item: dict[str, Any]) -> bool:
        return item.get("knowledge_type") == "demo"

    def validate(self, item: dict[str, Any]) -> ValidationResult:  # pragma: no cover - must not run
        raise AssertionError("executing validator ran in-process")


class Plugin(DomainPlugin):
    def validators(self) -> list[Validator]:
        return [UnitsValidator(), SimulationValidator()]
