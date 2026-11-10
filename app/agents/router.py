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

from collections import deque

from app.agents.base import BaseAgent
from app.agents.personality import Personality
from app.agents.prompts import REWRITE_SYSTEM, ROUTER_SYSTEM
from app.models.schemas import (AgentName, AgentResult, AgentTask, ChatResponse, Intent, RouteDecision, StepTrace)
from app.services.guardrails import check_input
from app.services.llm import LLMClient, LLMError, parse_json
from app.services import usage
from app.services.observability import log_event

logger = logging.getLogger("router")

PLANS: dict[Intent, list[AgentName]] = {
    Intent.PRODUCT_INFO: [AgentName.KNOWLEDGE],
    Intent.GENERAL: [AgentName.KNOWLEDGE],
    Intent.CUSTOMER_ACCOUNT: [AgentName.CUSTOMER_SUPPORT],
    Intent.TROUBLESHOOTING: [AgentName.CUSTOMER_SUPPORT, AgentName.KNOWLEDGE],
    Intent.HUMAN: [AgentName.ESCALATION],
    Intent.SMALLTALK: [],
    Intent.OUT_OF_SCOPE: [],
}

_SMALLTALK = re.compile(
    r"^\s*(oi+|ol[aá]|hi+|hello|hey|e a[ií]|bom dia|boa tarde|boa noite|good (morning|afternoon|evening)|"
    r"obrigad[oa]|muito obrigad[oa]|valeu|thanks?( you)?|thank you( so much)?|tchau|bye|goodbye|at[eé] (logo|mais)|"
    r"ok(ay)?|beleza|perfeito|great|tudo bem\??|how are you\??)([\s,!.?]+(getnet|obrigad[oa]|thanks|tudo bem|:\)|!+))*[\s!.?:)]*$",
    re.I)
_THANKS = re.compile(r"obrigad|valeu|thank", re.I)
_BYE = re.compile(r"tchau|bye|at[eé] (logo|mais)", re.I)
_OUT_OF_SCOPE = re.compile(
    r"^\s*(please\s+)?(write|compose|create|generate|escreva|crie|componha|fa[cç]a)\s+(me\s+|para mim\s+)?(a|an|um|uma)?\s*"
    r"(poem|song|story|essay|joke|poema|m[uú]sica|can[cç][aã]o|hist[oó]ria|reda[cç][aã]o|piada|code|program|script|c[oó]digo)"
    r"|\b(homework|li[cç][aã]o de casa|my essay)\b", re.I)


def smalltalk_reply(message: str) -> str:
    pt = bool(re.search(r"ol[aá]|oi|obrigad|valeu|tchau|bom dia|boa (tarde|noite)|tudo bem|beleza|at[eé]", message, re.I))
    if _THANKS.search(message):
        return ("De nada! Se precisar de algo sobre produtos Getnet, suas vendas ou sua maquininha, é só chamar."
                if pt else "You're welcome! If you need anything about Getnet products, your sales or your card machine, just ask.")
    if _BYE.search(message):
        return "Até logo! Estou por aqui quando precisar." if pt else "Goodbye! I'm here whenever you need me."
    return ("Olá! Sou o assistente virtual da Getnet. Posso ajudar com produtos e serviços Getnet, com suas vendas e "
            "recebimentos, ou com problemas na sua maquininha. Como posso ajudar?" if pt else
            "Hi! I'm Getnet's virtual assistant. I can help with Getnet products and services, your sales and settlements, "
            "or issues with your card machine. How can I help?")


OUT_OF_SCOPE_REPLY = ("That is outside what I can help with. I can answer questions about Getnet products and services, "
                      "check your sales, settlements and card machines, or look up general information such as weather "
                      "and exchange rates.")

_HUMAN = re.compile(r"(human|person|attendant|atendente|real agent|talk to (someone|an? agent)|falar com (algu[eé]m|um atendente)|ouvidoria)", re.I)
_DEVICE = re.compile(r"(card machine|machine|terminal|\bpos\b|maquininha|m[aá]quina)", re.I)
_PROBLEM = re.compile(r"(won'?t|not working|doesn'?t|can'?t|error|erro|declin|recus|negad|connect|offline|travou|problem|problema|n[aã]o (liga|conecta|funciona))", re.I)
_ACCOUNT = re.compile(r"\b(my|my own|minhas?|meus?)\b.*\b(sales|sale|money|deposit|settlement|transactions?|payments?|vendas?|dinheiro|repasse|recebimentos?)\b"
                      r"|\b(deposited|yesterday'?s sales|vendas de ontem|cair na (minha )?conta)\b", re.I)
_GENERAL = re.compile(r"(weather|forecast|temperature|exchange rate|c[aâ]mbio|cota[cç][aã]o|previs[aã]o do tempo|news|not[ií]cias)", re.I)
_PRODUCT_Q = re.compile(r"^\s*(do i need|can i|how does|how do|how many|what is|what's the difference|what are|is it possible|preciso|posso|como funciona|quantas?)\b", re.I)
_GETNET = re.compile(r"getnet|pix|link|maquininha|crediário|crediario|antecipa|get smart|get cl[aá]ssica|receivables|"
                     r"\bget (tap|mini|conta|checkout|smart)\b|card machine|\bpos\b|\btef\b|maquin|boleto|"
                     r"\b(fee|fees|taxa|taxas|tarifa|plano|plan|settlement|liquida|recebimento|parcel|installment)", re.I)


_FOLLOWUP_LEAD = re.compile(
    r"^\s*(e|and|what about|how about|e quanto|e a|e o|e as|e os|é a|é o|e[ée] a|it'?s (the|a|an)|is it|also|tamb[eé]m|"
    r"mas|but|and the|e se|e para|e pra|same|mesm[ao])\b", re.I)
DEFAULT_REASON = "default: Getnet products/services"
_ACCOUNT_DATA = re.compile(
    r"\b(yesterday|today'?s|last (week|month)|ontem|hoje|semana passada|deposited|depositad[oa]s?|repasse|"
    r"my (transactions?|deposits?|settlements?)|minhas (transa[cç][oõ]es|vendas de)|was declined|foi recusad[oa])\b", re.I)
MAX_STEPS = 4  # hard bound on agent executions per request (plan + replans)
_EMBEDDED_PRODUCT_Q = re.compile(
    r"\b(how does|how do|how can|what is|what are|como funciona|o que [eé]|como (posso|fa[cç]o))\b[^?.!]*"
    r"(antecipa|credi[aá]rio|pix|link|get (smart|cl[aá]ssica|mini|tap)|taxa|fee|receivables)", re.I)


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
    if _SMALLTALK.match(message):
        intent, reason = Intent.SMALLTALK, "greeting / thanks / goodbye"
    elif _OUT_OF_SCOPE.search(message):
        intent, reason = Intent.OUT_OF_SCOPE, "task the assistant does not perform"
    elif _HUMAN.search(message):
        intent, reason = Intent.HUMAN, "explicit human request"
    elif _DEVICE.search(message) and _PROBLEM.search(message):
        intent, reason = Intent.TROUBLESHOOTING, "problem reported with merchant's machine/transactions"
    elif _PRODUCT_Q.search(message) and _GETNET.search(message):
        intent, reason = Intent.PRODUCT_INFO, "how-to/eligibility question about a Getnet product"
    elif _ACCOUNT.search(message):
        intent, reason = Intent.CUSTOMER_ACCOUNT, "question about merchant's own account data"
    elif _GENERAL.search(message) and not _GETNET.search(message):
        intent, reason = Intent.GENERAL, "general-purpose question (not about Getnet)"
    elif not _GETNET.search(message) and not _DEVICE.search(message) and len(message.split()) >= 4 and "?" in message:
        intent, reason = Intent.GENERAL, "question without Getnet/product vocabulary: web search"
    else:
        intent, reason = Intent.PRODUCT_INFO, DEFAULT_REASON
    return RouteDecision(intent=intent, agents=PLANS[intent], reason=reason, method="rules")


class RouterAgent:
    name = AgentName.ROUTER

    def __init__(self, llm: LLMClient, agents: dict[AgentName, BaseAgent], personality: Personality,
                 use_llm_routing: bool = True, replan_budget_s: float = 150.0):
        self.llm, self.agents, self.personality = llm, agents, personality
        self.use_llm_routing = use_llm_routing
        self.replan_budget_s = replan_budget_s

    def replan(self, intent: Intent, message: str, last: AgentResult, results: list[AgentResult],
               pending: list[AgentName], elapsed_s: float) -> list[tuple[AgentName, str]]:
        """Bounded deterministic supervisor: observe the last result, propose next steps.

        Explicit transitions only (no LLM): every proposal is logged in metadata.replans. Proposals
        for agents that already ran or are already pending are discarded (no repeated actions).
        """
        done = {r.agent for r in results} | set(pending)
        props: list[tuple[AgentName, str]] = []
        if last.agent == AgentName.CUSTOMER_SUPPORT and last.status == "error" and intent in (
                Intent.TROUBLESHOOTING, Intent.CUSTOMER_ACCOUNT):
            props.append((AgentName.ESCALATION, "account data unavailable (support agent failed): human follow-up needed"))
        if last.agent == AgentName.CUSTOMER_SUPPORT and last.status != "error" and intent == Intent.CUSTOMER_ACCOUNT \
                and _EMBEDDED_PRODUCT_Q.search(message):
            props.append((AgentName.KNOWLEDGE, "message also contains a Getnet product question"))
        if last.agent == AgentName.KNOWLEDGE and last.status != "error" and intent == Intent.PRODUCT_INFO \
                and _ACCOUNT_DATA.search(message):
            props.append((AgentName.CUSTOMER_SUPPORT, "message also refers to the merchant's own account data"))
        if elapsed_s > self.replan_budget_s:  # latency budget: only escalation may still be added
            props = [p for p in props if p[0] == AgentName.ESCALATION]
        return [p for p in props if p[0] not in done and p[0] in self.agents]

    async def classify(self, message: str) -> RouteDecision:
        pre = classify_rules(message)
        if pre.intent in (Intent.SMALLTALK, Intent.OUT_OF_SCOPE):
            return pre  # deterministic, no LLM call needed
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
        usage.start()
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
        if not route.agents:  # smalltalk / out_of_scope: answered by the router, never escalated
            answer = smalltalk_reply(standalone) if route.intent == Intent.SMALLTALK else OUT_OF_SCOPE_REPLY
            meta |= {"route": route.model_dump(mode="json"), "steps": [], "evidence": [], "grounding": {}, "replans": [],
                     "usage": usage.totals(),
                     "personality_llm_merge": False, "latency_ms": int((time.perf_counter() - t0) * 1000)}
            log_event(logger, "request_completed", trace_id=trace_id, status="success", latency_ms=meta["latency_ms"])
            return ChatResponse(answer=answer, status="success", agent=AgentName.ROUTER, agents_invoked=[], metadata=meta)
        # Plan → execute → observe → (bounded) replan. Escalation always runs last so it can carry an AI draft.
        pending: deque[AgentName] = deque(route.agents)
        replans: list[dict] = []
        while pending and len(results) < MAX_STEPS:
            agent_name = pending.popleft()
            results.append(await self.agents[agent_name].run(new_task()))
            for nxt, why in self.replan(route.intent, standalone, results[-1], results, list(pending),
                                        time.perf_counter() - t0):
                (pending.append if nxt == AgentName.ESCALATION else pending.appendleft)(nxt)
                if AgentName.ESCALATION in pending:  # keep escalation last
                    pending.remove(AgentName.ESCALATION)
                    pending.append(AgentName.ESCALATION)
                replans.append({"after": agent_name.value, "action": f"add {nxt.value}", "reason": why})
        if pending:
            replans.append({"after": results[-1].agent.value, "action": "stop",
                            "reason": f"step limit {MAX_STEPS} reached; skipped {[a.value for a in pending]}"})

        # Human handoff ONLY when justified (BR-03): explicit request, a replanned escalation, or every agent failed.
        # Missing knowledge (zero retrieval hits) is answered honestly with an offer of a human — no auto-ticket.
        ran = {r.agent for r in results}
        needs_handoff = AgentName.ESCALATION not in ran and all(r.status == "error" for r in results)
        if needs_handoff and AgentName.ESCALATION in self.agents:
            results.append(await self.agents[AgentName.ESCALATION].run(new_task()))
            replans.append({"after": results[-2].agent.value, "action": "add escalation", "reason": "every agent failed"})
        meta["replans"] = replans

        answer, merged_with_llm = await self.personality.compose(standalone, results)
        status = _overall_status(results)
        sources = []
        for r in results:
            sources.extend(s for s in r.sources if s.url not in {x.url for x in sources})
        primary = next((r.agent for r in results if r.status in ("success", "escalated")), results[0].agent)
        meta |= {
            "route": route.model_dump(mode="json"),
            "steps": [StepTrace(agent=r.agent, status=r.status, latency_ms=r.latency_ms, tools=r.tools_used, error=r.error,
                                llm_calls=r.usage.get("llm_calls"), tokens_in=r.usage.get("tokens_in"),
                                tokens_out=r.usage.get("tokens_out")).model_dump(mode="json") for r in results],
            "usage": usage.totals(),
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
