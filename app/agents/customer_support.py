"""Customer Support Agent — plans and executes tools over the merchant's (simulated) data."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Callable

from app.agents.base import BaseAgent, clean_answer, language_instruction
from app.agents.prompts import SUPPORT_ANSWER_SYSTEM, SUPPORT_PLANNER_SYSTEM
from app.models.schemas import AgentName, AgentResult, AgentTask, Source
from app.services.llm import LLMClient, LLMError, parse_json
from app.tools import customer as ct

logger = logging.getLogger(__name__)

RULES = {
    "get_settlement_schedule": re.compile(r"(deposit|settle|money|payout|paid|receive|repasse|dep[oó]sit|cair na conta|receb|liquida)", re.I),
    "get_transaction_status": re.compile(r"(transa[cç]|declin|recus|negad|sale|venda|approved|aprovad|charge|error|erro)", re.I),
    "get_terminal_status": re.compile(r"(machine|terminal|pos\b|maquin|connect|internet|wi-?fi|chip|offline|signal|sinal)", re.I),
}


class CustomerSupportAgent(BaseAgent):
    name = AgentName.CUSTOMER_SUPPORT

    def __init__(self, llm: LLMClient, today: Callable[[], date] = date.today):
        self.llm, self.today = llm, today

    async def plan_tools(self, message: str) -> tuple[list[str], str]:
        """LLM JSON plan validated against the registry; deterministic rules fallback."""
        rules = [t for t, rx in RULES.items() if rx.search(message)]
        try:
            tools_desc = "\n".join(f"- {k}: {v}" for k, v in ct.TOOL_DESCRIPTIONS.items())
            raw = await self.llm.chat(SUPPORT_PLANNER_SYSTEM.format(tools=tools_desc), message, json_mode=True, max_tokens=80)
            planned = [t for t in parse_json(raw).get("tools", []) if t in ct.TOOLS]
            if planned:
                # union with rules: a cheap safety net against a small model missing an obvious tool
                return list(dict.fromkeys(planned + rules)), "llm"
        except (LLMError, ValueError, AttributeError) as e:
            logger.info("tool planner fallback to rules: %s", e)
        return (rules or ["get_transaction_status", "get_settlement_schedule"]), "rules"

    def _args(self, tool: str, message: str) -> dict:
        today = self.today()
        low = message.lower()
        if tool == "get_settlement_schedule":
            if re.search(r"\b(today|hoje)\b", low) and not re.search(r"yesterday|ontem", low):
                return {"sales_date": today.isoformat()}
            return {"sales_date": (today - timedelta(days=1)).isoformat()}
        if tool == "get_transaction_status" and re.search(r"declin|recus|negad", low):
            return {"status": "declined"}
        return {}

    async def _run(self, task: AgentTask) -> AgentResult:
        profile = ct.get_customer_profile(task.user_id)
        if not profile["found"]:
            return AgentResult(agent=self.name, status="partial", confidence=0.2, tools_used=["get_customer_profile"],
                               answer=f"I couldn't find a merchant account for user '{task.user_id}'. "
                                      "Please check the identifier or contact Getnet support.",
                               data={"profile": profile})
        tools, plan_method = await self.plan_tools(task.message)
        data: dict = {"profile": profile}
        for t in tools:
            data[t] = ct.TOOLS[t](task.user_id, today=self.today(), **self._args(t, task.message))
        user = (f"TODAY: {self.today().isoformat()}\nTOOL DATA:\n{json.dumps(data, ensure_ascii=False, indent=1)[:5000]}"
                f"\n\nMERCHANT MESSAGE: {task.message}" + language_instruction(task.message))
        try:
            answer = clean_answer(await self.llm.chat(SUPPORT_ANSWER_SYSTEM, user))
            if "simulated demo data" not in answer.lower():  # enforce disclosure regardless of model compliance
                answer += "\n\n(simulated demo data)"
            status = "success"
        except LLMError:
            answer = summarize(data)
            status = "partial"
        return AgentResult(agent=self.name, status=status, answer=answer, data=data | {"plan_method": plan_method},
                           tools_used=["get_customer_profile", *tools], confidence=0.9,
                           sources=[Source(title=f"Simulated merchant data: {t}", url=f"tool://{t}", type="tool") for t in tools])


def summarize(data: dict) -> str:
    """Deterministic, template-based summary used only when the LLM is unavailable."""
    lines = []
    if s := data.get("get_settlement_schedule"):
        for item in s["schedule"]:
            lines.append(f"Sales of {s['sales_date']}: R$ {item['amount_brl']:.2f} → deposit {item['deposit_date']} ({item['status']}).")
    if t := data.get("get_transaction_status"):
        for tx in [x for x in t["transactions"] if x["status"] == "declined"][:3]:
            lines.append(f"Transaction {tx['transaction_id']} declined (code {tx['decline_code']}): {tx['decline_reason']}")
    if m := data.get("get_terminal_status"):
        for term in m["terminals"]:
            lines.append(f"Terminal {term['terminal_id']} ({term['model']}): {term['status']}"
                         + (f" — {term['last_error']}" if term.get("last_error") else ""))
    return "\n".join(lines or ["No matching records found."]) + "\n(simulated demo data)"
