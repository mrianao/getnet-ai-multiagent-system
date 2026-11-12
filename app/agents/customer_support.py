"""Customer Support Agent — plans and executes tools over the merchant's (simulated) data."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Callable

from app.agents.base import BaseAgent, clean_answer, detect_language, language_instruction
from app.agents.prompts import SUPPORT_ANSWER_SYSTEM, SUPPORT_PLANNER_SYSTEM
from app.models.schemas import AgentName, AgentResult, AgentTask, Source
from app.rag.grounding import check_grounding, numbers, summary
from app.security.tenancy import authorize_tool_call, cross_merchant_reason, foreign_merchant_refs, refusal
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
        # Tenant isolation (defence layer 2): never run private tools for a request about another merchant,
        # even if the router check were bypassed. No tool is called, nothing about the other merchant is revealed.
        reason = cross_merchant_reason(task.message, task.user_id)
        if reason:
            return AgentResult(agent=self.name, status="blocked", answer=refusal(task.message), confidence=1.0,
                               data={"blocked_reason": reason, "evidence": []})
        profile = ct.get_customer_profile(task.user_id)
        if not profile["found"]:
            return AgentResult(agent=self.name, status="partial", confidence=0.2, tools_used=["get_customer_profile"],
                               answer=f"I couldn't find a merchant account for user '{task.user_id}'. "
                                      "Please check the identifier or contact Getnet support.",
                               data={"profile": profile})
        tools, plan_method = await self.plan_tools(task.message)
        data: dict = {"profile": profile}
        calls = []
        for t in tools:
            args = self._args(t, task.message)
            authorize_tool_call(task.user_id, task.user_id if "user_id" not in args else args["user_id"])
            data[t] = ct.TOOLS[t](task.user_id, today=self.today(), **args)
            calls.append({"type": "tool", "tool": t, "arguments": args, "merchant_id": task.user_id, "simulated": True})
        user = (f"TODAY: {self.today().isoformat()}\nTOOL DATA:\n{json.dumps(data, ensure_ascii=False, indent=1)[:5000]}"
                f"\n\nMERCHANT MESSAGE: {task.message}" + language_instruction(task.message))
        grounding = None
        facts = facts_block(data, task.message)
        try:
            answer = clean_answer(await self.llm.chat(SUPPORT_ANSWER_SYSTEM, user))
            violations = output_violations(answer, data, task.user_id)
            if violations:  # deterministic output check: discard the generated text, answer from the tool data
                logger.warning("support answer rejected: %s", violations)
                answer = summarize(data, task.message)
                data["output_check"] = {"rejected_llm_answer": True, "violations": violations}
            elif facts:
                answer = f"{facts}\n\n{answer}"  # verified facts first, so diagnoses survive any paraphrase
            # numeric check against the exact tool output; FLAG ONLY (derived totals are legitimate)
            report = check_grounding(answer, [{"text": json.dumps(data, ensure_ascii=False) + f" {self.today().isoformat()}"}],
                                     task.message, embed=None, citable=0)
            grounding = summary(report, [], "numeric vs tool output (identifiers/amounts enforced, prose not scored)")
            # For tool answers only NUMERIC support is meaningful: lexical overlap between Portuguese prose and
            # JSON keys is not evidence of anything (this produced the misleading "25 %" in manual tests).
            numeric = [c for c in report.claims if c.factual and numbers(c.text)]
            grounding["ratio"] = round(sum(1 for c in numeric if not c.unsupported_numbers) / len(numeric), 4) if numeric else None
            grounding["verdict"] = "grounded" if not report.unsupported_number_claims else "flagged"
            if "simulad" not in answer.lower():  # enforce disclosure regardless of model compliance
                answer += "\n\n" + ("(dados simulados de demonstração)" if detect_language(task.message) == "Portuguese"
                                     else "(simulated demo data)")
            status = "success"
        except LLMError:
            answer = summarize(data, task.message)
            status = "partial"
        extra = {"plan_method": plan_method, "evidence": calls, "diagnosis": diagnosis(data)}
        if grounding:
            extra["grounding"] = grounding
        return AgentResult(agent=self.name, status=status, answer=answer, data=data | extra,
                           tools_used=["get_customer_profile", *tools], confidence=0.9,
                           sources=[Source(title=f"Simulated merchant data: {t}", url=f"tool://{t}", type="tool") for t in tools])


_TX_ID = re.compile(r"\bTX[0-9A-F]{6,}\b")
_MONEY = re.compile(r"R\$\s*\d[\d.,]*", re.I)


def output_violations(answer: str, data: dict, merchant_id: str) -> list[str]:
    """Deterministic checks on a generated support answer (not prompt instructions).

    * names another merchant  → misattribution / cross-tenant leak
    * transaction id not present in the tool output → fabricated record
    * R$ amount absent from the tool output → fabricated or mis-computed figure
    """
    out = []
    foreign = foreign_merchant_refs(answer, merchant_id)
    if foreign:
        out.append(f"foreign merchant reference: {foreign}")
    blob = json.dumps(data, ensure_ascii=False)
    fake_ids = sorted(set(_TX_ID.findall(answer)) - set(_TX_ID.findall(blob)))
    if fake_ids:
        out.append(f"transaction ids not in tool output: {fake_ids}")
    known = numbers(blob)
    bad_amounts = [m for m in _MONEY.findall(answer) if numbers(m) and not numbers(m) <= known]
    if bad_amounts:
        out.append(f"amounts not in tool output: {bad_amounts}")
    return out


def diagnosis(data: dict) -> list[str]:
    """Structured findings passed to the Knowledge agent as its retrieval context (instead of free LLM prose)."""
    out = []
    for t in (data.get("get_terminal_status") or {}).get("terminals", []):
        if t["status"] != "online":
            out.append(f"{t['model']} {t['connectivity']} {t.get('last_error', '')}".strip())
    for tx in [x for x in (data.get("get_transaction_status") or {}).get("transactions", []) if x["status"] == "declined"][:2]:
        out.append(f"transação recusada código {tx['decline_code']} {tx['decline_reason']}")
    return out


def facts_block(data: dict, message: str) -> str:
    """Short deterministic statement of verified findings (terminal status), kept across turns."""
    term = data.get("get_terminal_status")
    if not term or not term.get("terminals"):
        return ""
    pt = detect_language(message) == "Portuguese"
    lines = []
    for t in term["terminals"]:
        state = "online" if t["status"] == "online" else "offline"
        err = f" — {t['last_error']}" if t.get("last_error") else ""
        lines.append(f"- {t['terminal_id']} ({t['model']}, {t['connectivity']}): {state}{err}")
    head = "**Status verificado dos seus terminais:**" if pt else "**Verified status of your terminals:**"
    return head + "\n" + "\n".join(lines)


def summarize(data: dict, message: str = "") -> str:
    """Deterministic, template-based summary of the AUTHENTICATED merchant's tool data.

    Used when the LLM is unavailable or when its answer fails the output checks.
    """
    pt = detect_language(message) == "Portuguese" if message else False
    lines = []
    if s := data.get("get_settlement_schedule"):
        for item in s["schedule"]:
            lines.append(f"Vendas de {s['sales_date']}: R$ {item['amount_brl']:.2f} → depósito em {item['deposit_date']} ({item['status']})."
                         if pt else f"Sales of {s['sales_date']}: R$ {item['amount_brl']:.2f} → deposit {item['deposit_date']} ({item['status']}).")
    if t := data.get("get_transaction_status"):
        for tx in [x for x in t["transactions"] if x["status"] == "declined"][:3]:
            lines.append(f"Transação {tx['transaction_id']} recusada (código {tx['decline_code']}): {tx['decline_reason']}"
                         if pt else f"Transaction {tx['transaction_id']} declined (code {tx['decline_code']}): {tx['decline_reason']}")
        if not lines:
            n = len(t["transactions"])
            lines.append(f"{n} transações recentes encontradas, nenhuma recusada." if pt else
                         f"{n} recent transactions found, none declined.")
    if m := data.get("get_terminal_status"):
        for term in m["terminals"]:
            lines.append(f"Terminal {term['terminal_id']} ({term['model']}): {term['status']}"
                         + (f" — {term['last_error']}" if term.get("last_error") else ""))
    head = "Dados da sua conta:" if pt else "Your account data:"
    tail = "(dados simulados de demonstração)" if pt else "(simulated demo data)"
    return "\n".join([head, *(lines or ["Nenhum registro encontrado." if pt else "No matching records found."]), tail])
