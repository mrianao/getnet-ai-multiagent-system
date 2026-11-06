"""LLM client — one OpenAI-compatible implementation (Ollama, Groq, OpenAI...).

Agents depend on the `LLMClient` protocol, so tests inject a FakeLLM.
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


def parse_json(text: str) -> dict[str, Any]:
    """Tolerant JSON extraction (small models sometimes wrap JSON in prose/markdown)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))
