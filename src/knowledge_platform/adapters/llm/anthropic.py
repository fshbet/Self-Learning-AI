"""Anthropic Messages API adapter. Structured output is obtained through a forced tool call with the JSON schema."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .base import LLMProvider, LLMResult

API_VERSION = "2023-06-01"


class AnthropicLLM(LLMProvider):
    name = "anthropic"

    def __init__(self, base_url: str, api_key: str | None, timeout: float = 300.0, max_tokens: int = 4096) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        headers = {"Content-Type": "application/json", "anthropic-version": API_VERSION}
        if api_key:
            headers["x-api-key"] = api_key
        self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)

    def _messages(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        started = time.perf_counter()
        resp = self._client.post("/v1/messages", json=payload)
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(f"{resp.status_code}: {resp.text[:300]}", request=resp.request, response=resp)
        return resp.json(), int((time.perf_counter() - started) * 1000)

    @staticmethod
    def _result(data: dict[str, Any], text: str, model: str, latency: int) -> LLMResult:
        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            model=data.get("model", model),
            prompt_tokens=int(usage.get("input_tokens", 0) or 0),
            completion_tokens=int(usage.get("output_tokens", 0) or 0),
            latency_ms=latency,
            raw={"id": data.get("id"), "stop_reason": data.get("stop_reason")},
        )

    def generate(self, *, system: str, user: str, model: str, temperature: float = 0.0) -> LLMResult:
        data, latency = self._messages(
            {
                "model": model,
                "max_tokens": self.max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
        )
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        return self._result(data, text, model, latency)

    def generate_json(
        self, *, system: str, user: str, model: str, schema: dict[str, Any], temperature: float = 0.0
    ) -> LLMResult:
        data, latency = self._messages(
            {
                "model": model,
                "max_tokens": self.max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "tools": [{"name": "emit", "description": "Return the structured result.", "input_schema": schema}],
                "tool_choice": {"type": "tool", "name": "emit"},
            }
        )
        for block in data.get("content", []):
            if block.get("type") == "tool_use":
                return self._result(data, json.dumps(block.get("input", {})), model, latency)
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        return self._result(data, text, model, latency)

    def available_models(self) -> list[str]:
        try:
            resp = self._client.get("/v1/models", params={"limit": 100})
            resp.raise_for_status()
            return sorted(m.get("id", "") for m in resp.json().get("data", []) if m.get("id"))
        except httpx.HTTPError:
            return []
