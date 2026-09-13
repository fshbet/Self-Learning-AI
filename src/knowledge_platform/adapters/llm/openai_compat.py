"""OpenAI-compatible chat adapter.

Covers OpenAI, Gemini's OpenAI-compatible endpoint, Groq, OpenRouter, Mistral, LM Studio, vLLM and others
that expose ``/v1/chat/completions`` and ``/v1/models``. Structured output uses ``response_format`` with a
JSON schema when the server accepts it and falls back to ``json_object`` + schema-in-prompt otherwise.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .base import LLMProvider, LLMResult


class OpenAICompatLLM(LLMProvider):
    name = "openai"

    def __init__(self, base_url: str, api_key: str | None, timeout: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)
        self._schema_supported: bool | None = None

    def _chat(self, payload: dict[str, Any]) -> LLMResult:
        started = time.perf_counter()
        resp = self._client.post("/chat/completions", json=payload)
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(f"{resp.status_code}: {resp.text[:300]}", request=resp.request, response=resp)
        data = resp.json()
        usage = data.get("usage") or {}
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        return LLMResult(
            text=content,
            model=data.get("model", payload["model"]),
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
            raw={"id": data.get("id")},
        )

    def generate(self, *, system: str, user: str, model: str, temperature: float = 0.0) -> LLMResult:
        return self._chat(
            {
                "model": model,
                "temperature": temperature,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            }
        )

    def generate_json(
        self, *, system: str, user: str, model: str, schema: dict[str, Any], temperature: float = 0.0
    ) -> LLMResult:
        base = {
            "model": model,
            "temperature": temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self._schema_supported is not False:
            try:
                res = self._chat(
                    base
                    | {
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {"name": "result", "schema": schema, "strict": False},
                        }
                    }
                )
                self._schema_supported = True
                return res
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code in (400, 422):
                    self._schema_supported = False  # server does not support json_schema; fall back
                else:
                    raise
        fallback = dict(base)
        fallback["messages"] = [
            {
                "role": "system",
                "content": system + "\n\nRespond ONLY with JSON matching this schema:\n" + json.dumps(schema),
            },
            {"role": "user", "content": user},
        ]
        fallback["response_format"] = {"type": "json_object"}
        return self._chat(fallback)

    def available_models(self) -> list[str]:
        try:
            resp = self._client.get("/models")
            resp.raise_for_status()
            return sorted(m.get("id", "") for m in resp.json().get("data", []) if m.get("id"))
        except httpx.HTTPError:
            return []
