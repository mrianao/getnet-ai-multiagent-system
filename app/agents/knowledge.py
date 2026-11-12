"""Knowledge Agent — RAG over Getnet's official websites + web tools for general questions."""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from app.agents.base import BaseAgent, clean_answer, context_summary, language_instruction
from app.agents.prompts import KNOWLEDGE_RAG_SYSTEM, KNOWLEDGE_WEB_SYSTEM
from app.models.schemas import AgentName, AgentResult, AgentTask, Intent, Source
from app.rag.grounding import apply_policy, check_grounding, summary
from app.rag.store import Retriever
from app.services.llm import LLMClient, LLMError
from app.tools.web import WebTools, detect_city, detect_currency_pair, detect_day_offset

logger = logging.getLogger(__name__)

WEATHER_RE = re.compile(r"\b(weather|forecast|rain|temperature|previs[aã]o do tempo|clima|chover|temperatura)\b", re.I)
FX_RE = re.compile(r"\b(exchange rate|c[aâ]mbio|cota[cç][aã]o|euro|dollar|d[oó]lar|currency)\b", re.I)


class KnowledgeAgent(BaseAgent):
    name = AgentName.KNOWLEDGE

    def __init__(self, llm: LLMClient, retriever: Retriever, web: WebTools, sim_threshold: float = 0.5):
        self.llm, self.retriever, self.web = llm, retriever, web
        self.sim_threshold = sim_threshold

    async def _run(self, task: AgentTask) -> AgentResult:
        if task.intent == Intent.GENERAL:
            return await self._answer_web(task)
        return await self._answer_rag(task)

    # ---------------- RAG ----------------
    async def _answer_rag(self, task: AgentTask) -> AgentResult:
        ctx = context_summary(task)
        # Retrieval query: the merchant's message + STRUCTURED diagnosis from the support tools (terminal error,
        # decline reason). The free-text support answer pulled unrelated "cartão" FAQ chunks into troubleshooting.
        diag = [d for r in task.context for d in r.data.get("diagnosis", [])]
        query = f"{task.message}\n{' '.join(diag)[:200]}" if diag else (task.message if not ctx else f"{task.message}\n{ctx[:300]}")
        hits, dropped = product_topic_filter(task.message, self.retriever.retrieve(query))
        evidence = [{"type": "rag", "rank": i, "chunk_id": h["id"], "url": h["url"], "title": h["title"], "text": h["text"],
                     "score": h["score"], "bm25": h.get("bm25"), "rrf": h.get("rrf")} for i, h in enumerate(hits, 1)]
        if not hits:
            # Missing knowledge is NOT an escalation trigger by itself: say so and offer a human.
            return AgentResult(agent=self.name, status="partial", confidence=0.0, tools_used=["rag_retrieve"],
                               answer="I couldn't find this information in Getnet's official pages. If you'd like, "
                                      "I can connect you with a Getnet specialist — just ask to talk to a human.",
                               data={"retrieved": 0, "evidence": [], "query": query})
        sources, blocks = [], []
        for i, h in enumerate(hits, 1):
            blocks.append(f"[{i}] (source: {h['title']} — {h['url']})\n{h['text']}")
            sources.append(Source(title=h["title"], url=h["url"], type="rag", score=h["score"]))
        user = "CONTEXT:\n" + "\n\n".join(blocks)
        if diag:  # structured findings only: with the support agent's prose the model copied/contradicted it
            user += "\n\nCUSTOMER CONTEXT (verified account findings):\n- " + "\n- ".join(diag)
        elif ctx:
            user += f"\n\nCUSTOMER CONTEXT:\n{ctx}"
        user += f"\n\nQUESTION: {task.message}" + language_instruction(task.message)
        confidence = hits[0]["score"]
        data = {"retrieved": len(hits), "evidence": evidence, "query": query, "dropped_off_product": dropped}
        try:
            raw = clean_answer(await self.llm.chat(KNOWLEDGE_RAG_SYSTEM, user))
        except LLMError as e:
            logger.warning("LLM unavailable, returning extractive answer: %s", e)
            data["grounding"] = {"verdict": "extractive", "method": "llm_unavailable"}
            return AgentResult(agent=self.name, status="partial", answer=_extractive(hits, "The language model is unavailable "
                               "right now."), sources=_dedupe(sources), tools_used=["rag_retrieve"], confidence=confidence,
                               data=data)
        # Grounding: evidence = retrieved chunks (+ upstream agent context, citable=False)
        ground_ev = evidence + ([{"text": ctx}] if ctx else [])
        report = check_grounding(raw, ground_ev, task.message, embed=self.retriever.store.embed_texts,
                                 sim_threshold=self.sim_threshold, citable=len(evidence))
        answer, removed = apply_policy(raw, report, remove_unsupported_numbers=True)
        status = "success"
        method = "citation+numeric(enforced)+embedding_proxy(reported)"
        if not answer.strip() or (report.factual_claims and len(removed) >= report.factual_claims):
            # every factual claim failed a DETERMINISTIC check (or nothing is left): quote the evidence instead.
            # Weak embedding similarity alone never triggers this fallback.
            answer, status = _extractive(hits, "I could not verify a generated answer against Getnet's official pages."), "partial"
            method += "; fallback=extractive"
        data["grounding"] = summary(report, removed, method)
        return AgentResult(agent=self.name, status=status, answer=answer, sources=_dedupe(sources),
                           tools_used=["rag_retrieve"], confidence=confidence, data=data)

    # ---------------- Web tools ----------------
    async def _answer_web(self, task: AgentTask) -> AgentResult:
        msg = task.message
        tools_used, results, sources = [], {}, []
        try:
            if WEATHER_RE.search(msg) and (city := detect_city(msg)):
                offset = detect_day_offset(msg)
                w = await self.web.get_weather(city, offset)
                w["requested_day"] = {0: "today", 1: "tomorrow"}.get(offset, f"+{offset} days")
                w["today_date"] = date.today().isoformat()
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
        tool_json = json.dumps(results, ensure_ascii=False, indent=1)[:4000]
        user = f"TOOL RESULTS:\n{tool_json}\n\nQUESTION: {msg}" + language_instruction(msg)
        evidence = [{"type": "web", "tool": t, "url": s_.url, "title": s_.title} for t in tools_used for s_ in sources[:1]] \
            if sources else [{"type": "web", "tool": t} for t in tools_used]
        data = results | {"evidence": evidence}
        try:
            raw = clean_answer(await self.llm.chat(KNOWLEDGE_WEB_SYSTEM, user))
            report = check_grounding(raw, [{"text": tool_json}], msg, embed=None, citable=0)
            answer, removed = apply_policy(raw, report, remove_unsupported_numbers=True)
            if not answer.strip():
                answer = "Live data retrieved: " + tool_json[:600]
            data["grounding"] = summary(report, removed, "numeric(enforced) vs live tool results")
            status = "success"
        except LLMError:
            answer = "Live data retrieved (language model unavailable): " + json.dumps(results, ensure_ascii=False)[:600]
            status = "partial"
        return AgentResult(agent=self.name, status=status, answer=answer, sources=_dedupe(sources),
                           tools_used=tools_used, data=data, confidence=0.9)


def _dedupe(sources: list[Source]) -> list[Source]:
    seen, out = set(), []
    for s in sources:
        if s.url not in seen:
            seen.add(s.url)
            out.append(s)
    return out


def _extractive(hits: list[dict], lead: str) -> str:
    """Deterministic fallback: quote the two most relevant official excerpts with citations."""
    quotes = "\n".join(f"[{i}] «{h['text'][:300].strip()}»" for i, h in enumerate(hits[:2], 1))
    return f"{lead} Most relevant excerpts from Getnet's official website:\n{quotes}"


# Product pages of the corpus. A chunk from one product's page must not answer a question about another
# product: observed failures were card-activation steps (cartao-getnet) in an antecipação answer and
# Crediário conditions ("2 dias úteis, sem custo de antecipação") presented as antecipação conditions.
PRODUCT_TOPICS = {
    "antecipacao": (re.compile(r"antecipa|receivables? advance", re.I), ("antecipacao",)),
    "crediario": (re.compile(r"credi[aá]rio|installment credit", re.I), ("crediario",)),
    "pix": (re.compile(r"\bpix\b", re.I), ("/pix",)),
    "link": (re.compile(r"link de pagamento|payment link|whatsapp", re.I), ("link-de-pagamento", "pagamentos-pelo-whatsapp")),
    "tap": (re.compile(r"get tap|tap to pay", re.I), ("get-tap",)),
    "cartao": (re.compile(r"cart[aã]o getnet|getnet card|ativar o cart|card activation", re.I), ("cartao-getnet",)),
    "conta": (re.compile(r"get conta|\bconta\b|\baccount\b", re.I), ("get-conta", "conta-digital")),
    "tef": (re.compile(r"\btef\b", re.I), ("/tef",)),
}


def _page_topics(url: str) -> set[str]:
    return {name for name, (_, slugs) in PRODUCT_TOPICS.items() if any(sl in url for sl in slugs)}


def product_topic_filter(question: str, hits: list[dict]) -> tuple[list[dict], list[str]]:
    """Drop chunks from a DIFFERENT product's page when the question names specific products.

    Generic pages (home, FAQ hub, machines) are kept. Never empties the evidence: if every hit would be
    dropped, the original list is returned.
    """
    asked = {name for name, (rx, _) in PRODUCT_TOPICS.items() if rx.search(question)}
    if not asked:
        return hits, []
    kept = [h for h in hits if not (_page_topics(h["url"]) and not (_page_topics(h["url"]) & asked))]
    if not kept:
        return hits, []
    return kept, [h["url"] for h in hits if h not in kept]
