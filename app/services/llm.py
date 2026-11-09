"""LLM clients: one OpenAI-compatible implementation (Ollama, Groq, OpenAI...) and one for
Anthropic Claude (official `anthropic` SDK).

Agents depend on the `LLMClient` protocol, so tests inject a FakeLLM. `build_llm` picks the
implementation from `LLM_PROVIDER`.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    provider: str
    model: str

    async def chat(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int | None = None) -> str: ...


class OpenAICompatibleLLM:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.provider = settings.llm_provider
        self.model = settings.llm_model
        self._s = settings
        self._client = httpx.AsyncClient(
            base_url=settings.llm_base_url.rstrip("/"),
            timeout=settings.llm_timeout_s,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            transport=transport,
        )

    async def chat(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int | None = None) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": self._s.llm_temperature,
            "max_tokens": max_tokens or self._s.llm_max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            r = await self._client.post("/chat/completions", json=body)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            raise LLMError(f"{self.provider} call failed: {type(e).__name__}: {e}") from e

    async def ping(self) -> bool:
        try:
            r = await self._client.get("/models", timeout=3)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()


JSON_INSTRUCTION = "\n\nRespond with a single valid JSON object only, with no prose and no markdown fences."


class AnthropicLLM:
    """Claude via the official Anthropic SDK (Messages API).

    The Messages API has no `response_format`, so JSON mode adds an explicit instruction to the
    system prompt; `parse_json` already tolerates any stray wrapping.
    """

    def __init__(self, settings: Settings, client: Any | None = None):
        self.provider = settings.llm_provider
        self.model = settings.llm_model
        self._s = settings
        if client is None:
            import anthropic  # imported lazily: only needed when LLM_PROVIDER=anthropic

            client = anthropic.AsyncAnthropic(api_key=settings.llm_api_key or None,
                                              timeout=settings.llm_timeout_s)
        self._client = client

    async def chat(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int | None = None) -> str:
        import anthropic

        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens or self._s.llm_max_tokens,
                system=system + (JSON_INSTRUCTION if json_mode else ""),
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIError as e:
            raise LLMError(f"{self.provider} call failed: {type(e).__name__}: {e}") from e
        if response.stop_reason == "refusal":
            raise LLMError(f"{self.provider} declined the request")
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise LLMError(f"{self.provider} returned no text (stop_reason={response.stop_reason})")
        return text

    async def ping(self) -> bool:
        try:
            await self._client.models.retrieve(self.model)
            return True
        except Exception:  # health check only: any failure means "not reachable"
            return False

    async def aclose(self) -> None:
        await self._client.close()


def build_llm(settings: Settings) -> LLMClient:
    """Choose the LLM implementation from `LLM_PROVIDER`."""
    if settings.llm_provider.lower() == "anthropic":
        return AnthropicLLM(settings)
    return OpenAICompatibleLLM(settings)


def parse_json(text: str) -> dict[str, Any]:
    """Tolerant JSON extraction (small models sometimes wrap JSON in prose/markdown)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))
