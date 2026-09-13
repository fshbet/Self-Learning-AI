"""Ollama adapter (local models). Uses the /api/chat endpoint with structured outputs."""

from __future__ import annotations

import time
from typing import Any

import httpx

from .base import LLMProvider, LLMResult


class OllamaLLM(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str, timeout: float = 300.0, num_ctx: int = 8192) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.num_ctx = num_ctx
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    # ------------------------------------------------------------------ helpers
    def _chat(self, payload: dict[str, Any]) -> LLMResult:
        started = time.perf_counter()
        resp = self._client.post("/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        latency = int((time.perf_counter() - started) * 1000)
        return LLMResult(
            text=data.get("message", {}).get("content", ""),
            model=data.get("model", payload["model"]),
            prompt_tokens=int(data.get("prompt_eval_count", 0) or 0),
            completion_tokens=int(data.get("eval_count", 0) or 0),
            latency_ms=latency,
            raw={k: v for k, v in data.items() if k != "message"},
        )

    def _base_payload(self, *, system: str, user: str, model: str, temperature: float) -> dict[str, Any]:
        return {
            "model": model,
            "stream": False,
            # Reasoning models (qwen3, deepseek-r1) emit long "thinking" blocks; we
            # want deterministic, cheap structured output for pipeline stages.
            "think": False,
            "options": {"temperature": temperature, "num_ctx": self.num_ctx},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }

    # ------------------------------------------------------------------ API
    def generate(self, *, system: str, user: str, model: str, temperature: float = 0.0) -> LLMResult:
        return self._chat(self._base_payload(system=system, user=user, model=model, temperature=temperature))

    def generate_json(
        self, *, system: str, user: str, model: str, schema: dict[str, Any], temperature: float = 0.0
    ) -> LLMResult:
        payload = self._base_payload(system=system, user=user, model=model, temperature=temperature)
        payload["format"] = schema
        return self._chat(payload)

    def available_models(self) -> list[str]:
        try:
            resp = self._client.get("/api/tags")
            resp.raise_for_status()
            return sorted(m["name"] for m in resp.json().get("models", []))
        except httpx.HTTPError:
            return []
