import json

import httpx
import respx

from knowledge_platform.adapters.embeddings.openai_compat import OpenAICompatEmbeddings
from knowledge_platform.adapters.llm.anthropic import AnthropicLLM
from knowledge_platform.adapters.llm.openai_compat import OpenAICompatLLM

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


@respx.mock
def test_openai_compat_json_schema_then_fallback():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(400, json={"error": {"message": "response_format json_schema unsupported"}})
        return httpx.Response(
            200,
            json={
                "id": "x",
                "model": body["model"],
                "choices": [{"message": {"role": "assistant", "content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    respx.post("https://api.test/v1/chat/completions").mock(side_effect=handler)
    respx.get("https://api.test/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}]})
    )
    llm = OpenAICompatLLM("https://api.test/v1", api_key="sk-test")
    res = llm.generate_json(system="s", user="u", model="m", schema=SCHEMA)
    assert json.loads(res.text) == {"ok": True}
    assert res.prompt_tokens == 12 and res.completion_tokens == 3
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"]["type"] == "json_object" and "schema" in calls[1]["messages"][0]["content"]
    assert calls[0]["messages"][0]["role"] == "system"
    # second call skips json_schema straight away (remembered as unsupported)
    llm.generate_json(system="s", user="u", model="m", schema=SCHEMA)
    assert calls[2]["response_format"]["type"] == "json_object"
    assert llm.available_models() == ["a", "b"]
    sent = respx.calls[0].request
    assert sent.headers["Authorization"] == "Bearer sk-test"


@respx.mock
def test_anthropic_tool_use_json_and_models():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tool_choice"] == {"type": "tool", "name": "emit"}
        assert body["tools"][0]["input_schema"] == SCHEMA
        assert request.headers["x-api-key"] == "ak-test" and request.headers["anthropic-version"]
        return httpx.Response(
            200,
            json={
                "id": "msg",
                "model": body["model"],
                "stop_reason": "tool_use",
                "content": [{"type": "tool_use", "name": "emit", "input": {"ok": True}}],
                "usage": {"input_tokens": 20, "output_tokens": 5},
            },
        )

    respx.post("https://api.anthropic.test/v1/messages").mock(side_effect=handler)
    respx.get("https://api.anthropic.test/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "claude-x"}, {"id": "claude-a"}]})
    )
    llm = AnthropicLLM("https://api.anthropic.test", api_key="ak-test")
    res = llm.generate_json(system="s", user="u", model="claude-x", schema=SCHEMA)
    assert json.loads(res.text) == {"ok": True} and res.prompt_tokens == 20
    assert llm.available_models() == ["claude-a", "claude-x"]


@respx.mock
def test_openai_compat_embeddings_dimension_check():
    respx.post("https://api.test/v1/embeddings").mock(
        return_value=httpx.Response(
            200, json={"data": [{"index": 1, "embedding": [0.0] * 4}, {"index": 0, "embedding": [1.0] * 4}]}
        )
    )
    emb = OpenAICompatEmbeddings("https://api.test/v1", api_key=None, model="e", dimension=4)
    vectors = emb.embed(["a", "b"])
    assert vectors[0] == [1.0] * 4 and vectors[1] == [0.0] * 4  # re-ordered by index
    bad = OpenAICompatEmbeddings("https://api.test/v1", api_key=None, model="e", dimension=8)
    try:
        bad.embed(["a"])
        raise AssertionError("dimension mismatch not detected")
    except ValueError:
        pass
