"""LLM client selection and the Anthropic client (no network: the SDK client is faked)."""
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services.llm import AnthropicLLM, LLMError, OpenAICompatibleLLM, build_llm, parse_json


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs: dict = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def fake_client(text: str = '{"intent": "general"}', stop_reason: str = "end_turn"):
    content = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(messages=FakeMessages(SimpleNamespace(content=content, stop_reason=stop_reason)))


def anthropic_settings() -> Settings:
    return Settings(llm_provider="anthropic", llm_model="claude-opus-5-5", llm_api_key="test-key")


def test_build_llm_picks_provider():
    assert isinstance(build_llm(anthropic_settings()), AnthropicLLM)
    assert isinstance(build_llm(Settings(llm_provider="ollama")), OpenAICompatibleLLM)


async def test_anthropic_chat_returns_text_and_adds_json_instruction():
    client = fake_client()
    llm = AnthropicLLM(anthropic_settings(), client=client)
    out = await llm.chat("You are a router.", "hello", json_mode=True)
    assert parse_json(out) == {"intent": "general"}
    sent = client.messages.kwargs
    assert sent["model"] == "claude-opus-5-5"
    assert sent["messages"] == [{"role": "user", "content": "hello"}]
    assert sent["system"].startswith("You are a router.") and "JSON" in sent["system"]


async def test_anthropic_refusal_raises_llm_error():
    llm = AnthropicLLM(anthropic_settings(), client=fake_client(stop_reason="refusal"))
    with pytest.raises(LLMError):
        await llm.chat("sys", "user")
