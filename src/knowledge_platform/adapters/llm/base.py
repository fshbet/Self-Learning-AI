"""Provider-agnostic LLM interface.

The platform only ever needs two operations: free-text generation and
schema-constrained JSON generation. Everything else (retries, accounting,
prompt versioning) is layered on top in the core.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def generate(self, *, system: str, user: str, model: str, temperature: float = 0.0) -> LLMResult:
        """Free-form completion."""

    @abstractmethod
    def generate_json(
        self, *, system: str, user: str, model: str, schema: dict[str, Any], temperature: float = 0.0
    ) -> LLMResult:
        """Completion constrained to ``schema`` (JSON Schema). ``text`` holds the JSON string."""

    @abstractmethod
    def available_models(self) -> list[str]:
        """Models the provider can serve right now (for health checks / UI)."""
