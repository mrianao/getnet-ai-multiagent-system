"""Personality layer (from the PDF diagram): consolidates agent outputs into one on-brand answer."""
from __future__ import annotations

from app.agents.base import clean_answer
from app.agents.prompts import PERSONALITY_MERGE_SYSTEM
from app.models.schemas import AgentResult
from app.services.llm import LLMClient, LLMError


class Personality:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def compose(self, message: str, results: list[AgentResult]) -> tuple[str, bool]:
        """Returns (answer, used_llm).

        v2: multi-agent answers are composed DETERMINISTICALLY (account diagnosis → official guidance →
        handoff). The v1 LLM merge was measured to drop the Customer Support diagnosis on the 3B model
        (eval pdf05), and it cost one extra LLM call. Each part is already on-brand and grounded by
        its own agent; citations [n] keep pointing at the Knowledge evidence. `merge_with_llm()` remains
        available for larger models.
        """
        useful = [r for r in results if r.answer]
        if not useful:
            return ("Sorry, I couldn't process your request right now. Please try again or contact Getnet support.", False)
        if len(useful) == 1:
            return useful[0].answer, False
        return self.structured(message, useful), False

    @staticmethod
    def structured(message: str, useful: list[AgentResult]) -> str:
        from app.agents.base import detect_language
        from app.models.schemas import AgentName

        pt = detect_language(message) == "Portuguese"
        titles = {AgentName.CUSTOMER_SUPPORT: "Sua conta" if pt else "Your account",
                  AgentName.KNOWLEDGE: "Orientações oficiais da Getnet" if pt else "Official Getnet guidance",
                  AgentName.ESCALATION: "Atendimento humano" if pt else "Human support"}
        order = [AgentName.CUSTOMER_SUPPORT, AgentName.KNOWLEDGE, AgentName.ESCALATION]
        parts = sorted(useful, key=lambda r: order.index(r.agent) if r.agent in order else 99)
        return "\n\n".join(f"**{titles.get(r.agent, r.agent.value)}**\n{r.answer.strip()}" for r in parts)

    async def merge_with_llm(self, message: str, results: list[AgentResult]) -> tuple[str, bool]:
        """v1 behaviour (LLM merge) — kept for larger models; not used by default."""
        useful = [r for r in results if r.answer]
        parts = "\n\n".join(f"### {r.agent.value} answer\n{r.answer}" for r in useful)
        try:
            merged = await self.llm.chat(PERSONALITY_MERGE_SYSTEM, f"USER MESSAGE: {message}\n\n{parts}", max_tokens=600)
            merged = clean_answer(merged)
            if any("simulated demo data" in r.answer for r in useful) and "simulated demo data" not in merged.lower():
                merged += "\n\n(simulated demo data)"
            return merged, True
        except LLMError:
            return "\n\n".join(r.answer for r in useful), False
