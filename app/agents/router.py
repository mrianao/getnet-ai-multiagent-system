"""Router Agent — entry point and orchestrator.

1. Input guardrails.
2. Intent classification: LLM (JSON mode, Pydantic-validated) → deterministic rules fallback.
3. Plan = ordered list of agents for the intent (deterministic mapping, auditable).
4. Sequential execution with context propagation (AgentTask.context).
5. Handoff to the Escalation agent on failure / no answer.
6. Personality layer consolidates → ChatResponse with full trace.
"""
from __future__ import annotations

import logging
import re
import time
import uuid

from pydantic import ValidationError

from app.agents.base import BaseAgent
from app.agents.personality import Personality
from app.agents.prompts import REWRITE_SYSTEM, ROUTER_SYSTEM
from app.models.schemas import (AgentName, AgentResult, AgentTask, ChatResponse, Intent, RouteDecision, StepTrace)
from app.services.guardrails import check_input
from app.services.llm import LLMClient, LLMError, parse_json
from app.services.observability import log_event

logger = logging.getLogger("router")

PLANS: dict[Intent, list[AgentName]] = {
    Intent.PRODUCT_INFO: [AgentName.KNOWLEDGE],
    Intent.GENERAL: [AgentName.KNOWLEDGE],
    Intent.CUSTOMER_ACCOUNT: [AgentName.CUSTOMER_SUPPORT],
    Intent.TROUBLESHOOTING: [AgentName.CUSTOMER_SUPPORT, AgentName.KNOWLEDGE],
    Intent.HUMAN: [AgentName.ESCALATION],
}

_HUMAN = re.compile(r"(human|person|attendant|atendente|real agent|talk to (someone|an? agent)|falar com (algu[eé]m|um atendente)|ouvidoria)", re.I)
_DEVICE = re.compile(r"(card machine|machine|terminal|\bpos\b|maquininha|m[aá]quina)", re.I)
_PROBLEM = re.compile(r"(won'?t|not working|doesn'?t|can'?t|error|erro|declin|recus|negad|connect|offline|travou|problem|problema|n[aã]o (liga|conecta|funciona))", re.I)
_ACCOUNT = re.compile(r"\b(my|my own|minhas?|meus?)\b.*\b(sales|sale|money|deposit|settlement|transactions?|payments?|vendas?|dinheiro|repasse|recebimentos?)\b"
                      r"|\b(deposited|yesterday'?s sales|vendas de ontem|cair na (minha )?conta)\b", re.I)
_GENERAL = re.compile(r"(weather|forecast|temperature|exchange rate|c[aâ]mbio|cota[cç][aã]o|previs[aã]o do tempo|news|not[ií]cias)", re.I)
_PRODUCT_Q = re.compile(r"^\s*(do i need|can i|how does|how do|how many|what is|what's the difference|what are|is it possible|preciso|posso|como funciona|quantas?)\b", re.I)
_GETNET = re.compile(r"getnet|pix|link|maquininha|crediário|crediario|antecipa|get smart|get cl[aá]ssica|receivables", re.I)


_FOLLOWUP_LEAD = re.compile(
    r"^\s*(e|and|what about|how about|e quanto|e a|e o|e as|e os|é a|é o|e[ée] a|it'?s (the|a|an)|is it|also|tamb[eé]m|"
    r"mas|but|and the|e se|e para|e pra|same|mesm[ao])\b", re.I)
DEFAULT_REASON = "default: Getnet products/services"


def is_followup(message: str, history: list[dict] | None) -> bool:
    """Deterministic follow-up detector: only consulted when the conversation has history.

    A message is treated as a follow-up when it is short and matches no explicit routing rule
    (e.g. "É a Smart.", "E a de ontem?"), or when it opens with an elliptical lead ("and what about…").
    Self-contained messages keep their original wording, so single-turn behaviour is unchanged.
    """
    if not history or not any(h["role"] == "user" for h in history):
        return False
    words = len(message.split())
    explicit = classify_rules(message).reason != DEFAULT_REASON
    return (words <= 6 and not explicit) or (bool(_FOLLOWUP_LEAD.match(message)) and words <= 14)


def classify_rules(message: str) -> RouteDecision:
    if _HUMAN.search(message):
        intent, reason = Intent.HUMAN, "explicit human request"
    elif _DEVICE.search(message) and _PROBLEM.search(message):
        intent, reason = Intent.TROUBLESHOOTING, "problem reported with merchant's machine/transactions"
    elif _PRODUCT_Q.search(message) and _GETNET.search(message):
        intent, reason = Intent.PRODUCT_INFO, "how-to/eligibility question about a Getnet product"
    elif _ACCOUNT.search(message):
        intent, reason = Intent.CUSTOMER_ACCOUNT, "question about merchant's own account data"
    elif _GENERAL.search(message) and not _GETNET.search(message):
        intent, reason = Intent.GENERAL, "general-purpose question (not about Getnet)"
    else:
        intent, reason = Intent.PRODUCT_INFO, DEFAULT_REASON
    return RouteDecision(intent=intent, agents=PLANS[intent], reason=reason, method="rules")


class RouterAgent:
    name = AgentName.ROUTER

    def __init__(self, llm: LLMClient, agents: dict[AgentName, BaseAgent], personality: Personality,
                 escalation_threshold: float = 0.0, use_llm_routing: bool = True):
        self.llm, self.agents, self.personality = llm, agents, personality
        self.escalation_threshold = escalation_threshold
        self.use_llm_routing = use_llm_routing

    async def classify(self, message: str) -> RouteDecision:
        if self.use_llm_routing:
            try:
                raw = await self.llm.chat(ROUTER_SYSTEM, message, json_mode=True, max_tokens=60)
                data = parse_json(raw)
                intent = Intent(data["intent"])
                return RouteDecision(intent=intent, agents=PLANS[intent], reason=str(data.get("reason", ""))[:200], method="llm")
            except (LLMError, ValueError, KeyError, TypeError, ValidationError) as e:
                logger.info("LLM routing failed, using rules: %s", e)
        return classify_rules(message)

    async def resolve_followup(self, message: str, history: list[dict] | None) -> tuple[str, dict]:
        """Turn an elliptical follow-up into a standalone message (LLM rewrite → deterministic fallback)."""
        info = {"history_messages": len(history or []), "followup": False, "rewrite_method": None}
        if not is_followup(message, history):
            return message, info
        info["followup"] = True
        last_user = next((h["content"] for h in reversed(history) if h["role"] == "user"), "")
        convo = " | ".join(f"{h['role']}: {h['content'][:300]}" for h in history[-4:])
        try:
            raw = await self.llm.chat(REWRITE_SYSTEM, f"Conversation: {convo}\nFollow-up: {message}", json_mode=True,
                                      max_tokens=120)
            standalone = str(parse_json(raw).get("standalone", "")).strip()
            if 3 <= len(standalone) <= 400 and check_input(standalone).allowed:
                info["rewrite_method"] = "llm"
                info["standalone_message"] = standalone
                return standalone, info
        except (LLMError, ValueError, AttributeError, TypeError) as e:
            logger.info("follow-up rewrite fallback: %s", e)
        standalone = f"{last_user} {message}".strip()[:600]
        info["rewrite_method"] = "concat"
        info["standalone_message"] = standalone
        return standalone, info

    async def handle(self, message: str, user_id: str, history: list[dict] | None = None,
                     conversation_id: str | None = None) -> ChatResponse:
        trace_id = str(uuid.uuid4())
        t0 = time.perf_counter()
        guard = check_input(message)
        meta = {"trace_id": trace_id, "llm_provider": self.llm.provider, "llm_model": self.llm.model,
                "guardrails": {"pii_masked": guard.pii_masked, "flags": guard.flags}}
        if not guard.allowed:
            log_event(logger, "request_blocked", trace_id=trace_id, flags=guard.flags)
            return ChatResponse(
                answer="I'm sorry, I can't help with that request. I can help with Getnet products, your sales, "
                       "settlements and card machines.",
                status="blocked", agent=AgentName.ROUTER, agents_invoked=[],
                metadata=meta | {"blocked_reason": guard.reason, "latency_ms": int((time.perf_counter() - t0) * 1000)})

        standalone, conv_info = await self.resolve_followup(guard.sanitized, history)
        meta["conversation"] = conv_info
        route = await self.classify(standalone)
        log_event(logger, "routed", trace_id=trace_id, intent=route.intent.value, agents=[a.value for a in route.agents],
                  method=route.method, followup=conv_info["followup"])

        def new_task(intent=route.intent):
            return AgentTask(trace_id=trace_id, user_id=user_id, message=standalone, intent=intent, context=list(results),
                             conversation_id=conversation_id, history=list(history or []))

        results: list[AgentResult] = []
        for agent_name in route.agents:
            results.append(await self.agents[agent_name].run(new_task()))

        # Human handoff when nothing useful could be produced (BR-03)
        needs_handoff = route.intent != Intent.HUMAN and (
            all(r.status == "error" for r in results)
            or (route.intent == Intent.PRODUCT_INFO and max(r.confidence for r in results) <= self.escalation_threshold)
        )
        if needs_handoff and AgentName.ESCALATION in self.agents:
            results.append(await self.agents[AgentName.ESCALATION].run(new_task()))

        answer, merged_with_llm = await self.personality.compose(standalone, results)
        status = _overall_status(results)
        sources = []
        for r in results:
            sources.extend(s for s in r.sources if s.url not in {x.url for x in sources})
        primary = next((r.agent for r in results if r.status in ("success", "escalated")), results[0].agent)
        meta |= {
            "route": route.model_dump(mode="json"),
            "steps": [StepTrace(agent=r.agent, status=r.status, latency_ms=r.latency_ms, tools=r.tools_used, error=r.error).model_dump(mode="json") for r in results],
            "personality_llm_merge": merged_with_llm,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }
        meta["evidence"] = [e | {"agent": r.agent.value, "trace_id": trace_id} for r in results for e in r.data.get("evidence", [])]
        meta["grounding"] = {r.agent.value: r.data["grounding"] for r in results if r.data.get("grounding")}
        for r in results:
            if r.agent == AgentName.ESCALATION:
                meta["handoff_ticket"] = r.data.get("ticket", {}).get("ticket_id")
        log_event(logger, "request_completed", trace_id=trace_id, status=status, latency_ms=meta["latency_ms"])
        return ChatResponse(answer=answer, status=status, agent=primary, agents_invoked=[r.agent for r in results],
                            sources=sources, metadata=meta)


def _overall_status(results: list[AgentResult]) -> str:
    statuses = {r.status for r in results}
    if "escalated" in statuses:
        return "escalated"
    if statuses == {"success"}:
        return "success"
    if statuses == {"error"}:
        return "error"
    return "partial"
