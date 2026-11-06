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
        """Returns (answer, used_llm). Single answers are already on-brand → no extra LLM call."""
        useful = [r for r in results if r.answer]
        if not useful:
            return ("Sorry, I couldn't process your request right now. Please try again or contact Getnet support.", False)
        if len(useful) == 1:
            return useful[0].answer, False
        parts = "\n\n".join(f"### {r.agent.value} answer\n{r.answer}" for r in useful)
        try:
            merged = await self.llm.chat(PERSONALITY_MERGE_SYSTEM, f"USER MESSAGE: {message}\n\n{parts}", max_tokens=600)
            merged = clean_answer(merged)
            if any("simulated demo data" in r.answer for r in useful) and "simulated demo data" not in merged.lower():
                merged += "\n\n(simulated demo data)"
            return merged, True
        except LLMError:
            return "\n\n".join(r.answer for r in useful), False
