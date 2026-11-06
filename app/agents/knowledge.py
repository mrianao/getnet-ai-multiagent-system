"""Knowledge Agent — RAG over Getnet's official websites + web tools for general questions."""
from __future__ import annotations

import json
import logging
import re

from app.agents.base import BaseAgent, clean_answer, context_summary, language_instruction
from app.agents.prompts import KNOWLEDGE_RAG_SYSTEM, KNOWLEDGE_WEB_SYSTEM
from app.models.schemas import AgentName, AgentResult, AgentTask, Intent, Source
from app.rag.store import Retriever
from app.services.llm import LLMClient, LLMError
from app.tools.web import WebTools, detect_city, detect_currency_pair, detect_day_offset

logger = logging.getLogger(__name__)

WEATHER_RE = re.compile(r"\b(weather|forecast|rain|temperature|previs[aã]o do tempo|clima|chover|temperatura)\b", re.I)
FX_RE = re.compile(r"\b(exchange rate|c[aâ]mbio|cota[cç][aã]o|euro|dollar|d[oó]lar|currency)\b", re.I)


class KnowledgeAgent(BaseAgent):
    name = AgentName.KNOWLEDGE

    def __init__(self, llm: LLMClient, retriever: Retriever, web: WebTools):
        self.llm, self.retriever, self.web = llm, retriever, web

    async def _run(self, task: AgentTask) -> AgentResult:
        if task.intent == Intent.GENERAL:
            return await self._answer_web(task)
        return await self._answer_rag(task)

    # ---------------- RAG ----------------
    async def _answer_rag(self, task: AgentTask) -> AgentResult:
        ctx = context_summary(task)
        query = task.message if not ctx else f"{task.message}\n{ctx[:300]}"
        hits = self.retriever.retrieve(query)
        if not hits:
            return AgentResult(agent=self.name, status="partial", confidence=0.0, tools_used=["rag_retrieve"],
                               answer="I couldn't find this information in Getnet's official knowledge base.",
                               data={"retrieved": 0})
        sources, blocks = [], []
        for i, h in enumerate(hits, 1):
            blocks.append(f"[{i}] (source: {h['title']} — {h['url']})\n{h['text']}")
            sources.append(Source(title=h["title"], url=h["url"], type="rag", score=h["score"]))
        user = f"CONTEXT:\n" + "\n\n".join(blocks)
        if ctx:
            user += f"\n\nCUSTOMER CONTEXT:\n{ctx}"
        user += f"\n\nQUESTION: {task.message}" + language_instruction(task.message)
        confidence = hits[0]["score"]
        try:
            answer = clean_answer(await self.llm.chat(KNOWLEDGE_RAG_SYSTEM, user))
            status = "success"
        except LLMError as e:
            logger.warning("LLM unavailable, returning extractive answer: %s", e)
            answer = "The language model is unavailable right now. Most relevant excerpts from Getnet's website:\n" + \
                "\n".join(f"[{i}] {h['text'][:300]}" for i, h in enumerate(hits[:2], 1))
            status = "partial"
        return AgentResult(agent=self.name, status=status, answer=answer, sources=_dedupe(sources),
                           tools_used=["rag_retrieve"], confidence=confidence, data={"retrieved": len(hits)})

    # ---------------- Web tools ----------------
    async def _answer_web(self, task: AgentTask) -> AgentResult:
        msg = task.message
        tools_used, results, sources = [], {}, []
        try:
            if WEATHER_RE.search(msg) and (city := detect_city(msg)):
                w = await self.web.get_weather(city, detect_day_offset(msg))
                tools_used.append("get_weather")
                results["weather"] = w
                sources.append(Source(title=f"{w['provider']} forecast — {w['city']}", url=w["source_url"], type="web"))
            elif FX_RE.search(msg):
                base, quote = detect_currency_pair(msg)
                fx = await self.web.get_exchange_rate(base, quote)
                tools_used.append("get_exchange_rate")
                results["exchange_rate"] = fx
                sources.append(Source(title=fx["provider"], url=fx["source_url"], type="web"))
        except Exception as e:  # noqa: BLE001 — degrade to generic web search
            logger.warning("specialised web tool failed: %s", e)
            results["tool_error"] = str(e)
        if not tools_used or "tool_error" in results:
            hits = await self.web.web_search(msg)
            tools_used.append("web_search")
            results["web_search"] = hits
            sources += [Source(title=h["title"], url=h["url"], type="web") for h in hits if h.get("url")]
        if not any(results.get(k) for k in ("weather", "exchange_rate", "web_search")):
            return AgentResult(agent=self.name, status="partial", confidence=0.0, tools_used=tools_used,
                               answer="I couldn't retrieve live information for this question right now. Please try again later.")
        user = f"TOOL RESULTS:\n{json.dumps(results, ensure_ascii=False, indent=1)[:4000]}\n\nQUESTION: {msg}" + language_instruction(msg)
        try:
            answer = clean_answer(await self.llm.chat(KNOWLEDGE_WEB_SYSTEM, user))
            status = "success"
        except LLMError:
            answer = "Live data retrieved (language model unavailable): " + json.dumps(results, ensure_ascii=False)[:600]
            status = "partial"
        return AgentResult(agent=self.name, status=status, answer=answer, sources=_dedupe(sources),
                           tools_used=tools_used, data=results, confidence=0.9)


def _dedupe(sources: list[Source]) -> list[Source]:
    seen, out = set(), []
    for s in sources:
        if s.url not in seen:
            seen.add(s.url)
            out.append(s)
    return out
