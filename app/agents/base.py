"""Agent base class: uniform run() with timing + error capture (agents never raise to the router)."""
from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod

from app.models.schemas import AgentName, AgentResult, AgentTask
from app.services import usage
from app.services.observability import log_event

logger = logging.getLogger("agents")


class BaseAgent(ABC):
    name: AgentName

    async def run(self, task: AgentTask) -> AgentResult:
        start = time.perf_counter()
        mark = usage.mark()
        try:
            result = await self._run(task)
        except Exception as e:  # noqa: BLE001 — isolate agent failures from the orchestration
            logger.exception("agent failure", extra={"extra_fields": {"agent": self.name.value, "trace_id": task.trace_id}})
            result = AgentResult(agent=self.name, status="error", error=f"{type(e).__name__}: {e}", confidence=0.0)
        result.latency_ms = int((time.perf_counter() - start) * 1000)
        result.usage = usage.totals(mark)
        log_event(logger, "agent_completed", trace_id=task.trace_id, agent=self.name.value, status=result.status,
                  tools=result.tools_used, latency_ms=result.latency_ms, confidence=result.confidence)
        return result

    @abstractmethod
    async def _run(self, task: AgentTask) -> AgentResult: ...


def context_summary(task: AgentTask, max_chars: int = 1200) -> str:
    parts = [f"[{r.agent.value}] {r.answer}" for r in task.context if r.answer]
    return "\n".join(parts)[:max_chars]


_PT = re.compile(r"\b(n[aã]o|voc[eê]|minha?s?|meus?|quando|como|qual|quais|preciso|vendas?|vender|obrigad[oa]|ol[aá]|para|bom|boa|"
                 r"est[aá]|maquininha|dinheiro|ontem|hoje|posso|quantas?|quantos?|parcelas?|em|no|na|nos|nas|do|da|dos|das|"
                 r"de|um|uma|que|[eé]|s[aã]o|com|pelo|pela|ter|tenho|funciona|conta|receber|cr[eé]dito|d[eé]bito)\b", re.I)


def detect_language(text: str) -> str:
    """Tiny heuristic (pt/en) — makes the answer language explicit for small LLMs."""
    hits = len(_PT.findall(text))
    return "Portuguese" if hits >= 2 or (hits == 1 and len(text.split()) <= 4) else "English"


def language_instruction(text: str) -> str:
    return f"\n\nIMPORTANT: The user wrote in {detect_language(text)}. Write your entire answer in {detect_language(text)}."


_PREAMBLE = re.compile(r"^\s*(here is (a|the|your) [^:\n]{0,40}:|based on the provided [^,\n]{0,40},\s*|according to the (provided )?(context|tool data)[^,\n]{0,30},\s*)", re.I)
_SIGNOFF = re.compile(r"\n+\s*(best regards|kind regards|sincerely|atenciosamente)[\s\S]*$", re.I)


def clean_answer(text: str) -> str:
    """Strip common small-model artefacts (meta preambles, letter sign-offs)."""
    out = _SIGNOFF.sub("", _PREAMBLE.sub("", text.strip())).strip()
    return out[:1].upper() + out[1:] if out else text
