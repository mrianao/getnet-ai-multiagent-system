"""Shared fixtures — the automated suite is fully OFFLINE and deterministic.

- FakeLLM: scripted, records every call (lets us assert prompts/context propagation).
- HashEmbedder: deterministic bag-of-words hashing embeddings (no model download).
- MockTransport: canned responses for Open-Meteo, Frankfurter, DuckDuckGo, Wikipedia.
- The RAG index is built from the REAL committed corpus snapshot (data/corpus/pages.jsonl).
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.agents.prompts import ROUTER_SYSTEM
from app.agents.router import classify_rules
from app.config import Settings
from app.container import build_container
from app.main import create_app
from app.rag.store import VectorStore
from app.services.llm import LLMError

FIXED_TODAY = date(2026, 11, 6)


class FakeLLM:
    provider, model = "fake", "fake-llm"

    def __init__(self, router_intent: dict[str, str] | None = None, fail: bool = False, router_raw: str | None = None,
                 planner_tools: list[str] | None = None):
        self.calls: list[dict] = []
        self.router_intent = router_intent or {}
        self.fail = fail
        self.router_raw = router_raw
        self.planner_tools = planner_tools

    async def chat(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int | None = None) -> str:
        self.calls.append({"system": system, "user": user, "json_mode": json_mode})
        if self.fail:
            raise LLMError("fake outage")
        if system == ROUTER_SYSTEM:
            if self.router_raw is not None:
                return self.router_raw
            for k, v in self.router_intent.items():
                if k in user:
                    return json.dumps({"intent": v, "reason": "scripted"})
            return json.dumps({"intent": classify_rules(user).intent.value, "reason": "fake mirrors rules"})
        if "tool planner" in system:
            return json.dumps({"tools": self.planner_tools or []})
        return f"FAKE ANSWER based on: {user[:400]}"


class HashEmbedder:
    dim = 512

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = np.zeros(self.dim)
            for w in re.findall(r"\w+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1
            n = np.linalg.norm(v)
            out.append((v / n if n else v).tolist())
        return out


def mock_handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host == "geocoding-api.open-meteo.com":
        return httpx.Response(200, json={"results": [{"name": "Porto Alegre", "latitude": -30.03, "longitude": -51.23,
                                                      "admin1": "Rio Grande do Sul", "country": "Brazil"}]})
    if host == "api.open-meteo.com":
        return httpx.Response(200, json={"daily": {"time": ["2026-11-06", "2026-11-07", "2026-11-08"],
                                                   "temperature_2m_max": [27.1, 24.3, 22.0], "temperature_2m_min": [17.2, 16.0, 15.1],
                                                   "precipitation_probability_max": [10, 80, 30], "weather_code": [1, 63, 3]}})
    if host == "wttr.in":
        hourly = [{"chanceofrain": "20", "weatherDesc": [{"value": "Sunny"}]}] * 8
        return httpx.Response(200, json={"nearest_area": [{"areaName": [{"value": "Porto Alegre"}], "region": [{"value": "RS"}],
                                                             "country": [{"value": "Brazil"}]}],
                                         "weather": [{"date": "2026-11-06", "maxtempC": "27", "mintempC": "17", "hourly": hourly},
                                                     {"date": "2026-11-07", "maxtempC": "25", "mintempC": "16", "hourly": hourly}]})
    if host == "api.frankfurter.app":
        return httpx.Response(200, json={"amount": 1.0, "base": "EUR", "date": "2026-11-05", "rates": {"BRL": 6.1234}})
    if host == "api.duckduckgo.com":
        return httpx.Response(200, json={"AbstractText": "", "RelatedTopics": []})
    if host == "en.wikipedia.org":
        return httpx.Response(200, json={"query": {"search": [{"title": "Brazil", "snippet": "Brazil is a <b>country</b>"}]}})
    return httpx.Response(404)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(chroma_dir=str(tmp_path / "chroma"), chroma_collection=f"test_{uuid.uuid4().hex[:8]}",
                    auto_ingest=True, tavily_api_key="", state_db_path=str(tmp_path / "state" / "app.db"))


@pytest.fixture
def http_client():
    return httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))


@pytest.fixture
def make_container(settings, http_client):
    def _make(llm=None, http=None):
        store = VectorStore(settings.chroma_dir, settings.chroma_collection, HashEmbedder())
        c = build_container(settings, llm=llm or FakeLLM(), http=http or http_client, store=store)
        # pin "today" for deterministic simulated data
        from app.models.schemas import AgentName
        c.router.agents[AgentName.CUSTOMER_SUPPORT].today = lambda: FIXED_TODAY
        return c
    return _make


@pytest.fixture
def container(make_container):
    return make_container()


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def merchant_token(container) -> str:
    return container.tokens.issue("test-merchant", "merchant", "cliente1988")[0]


@pytest.fixture
def client(container, merchant_token):
    """Authenticated as merchant cliente1988 (the original tests' user)."""
    with TestClient(create_app(container), headers=bearer(merchant_token)) as c:
        yield c


@pytest.fixture
def anon_client(container):
    with TestClient(create_app(container)) as c:
        yield c
