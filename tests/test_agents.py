"""Agents in isolation (fake LLM, mocked HTTP, real corpus with hash embeddings)."""
from app.models.schemas import AgentName, AgentResult, AgentTask, Intent
from tests.conftest import FakeLLM

A = AgentName


def task(msg, intent, user="cliente1988", context=None):
    return AgentTask(trace_id="t", user_id=user, message=msg, intent=intent, context=context or [])


async def test_knowledge_rag_answer_has_real_sources(container):
    r = await container.router.agents[A.KNOWLEDGE].run(task("How does antecipação de vendas work?", Intent.PRODUCT_INFO))
    assert r.status == "success" and r.tools_used == ["rag_retrieve"]
    assert r.sources and all(s.type == "rag" and "getnet" in s.url for s in r.sources)


async def test_knowledge_web_weather(container):
    r = await container.router.agents[A.KNOWLEDGE].run(task("What's the weather forecast in Porto Alegre tomorrow?", Intent.GENERAL))
    assert r.tools_used == ["get_weather"] and r.data["weather"]["date"] == "2026-11-07"
    assert r.sources[0].type == "web"


async def test_knowledge_web_generic_search(container):
    r = await container.router.agents[A.KNOWLEDGE].run(task("Who won the 2002 world cup?", Intent.GENERAL))
    assert r.tools_used == ["web_search"] and r.sources


async def test_support_agent_uses_tools_and_discloses_simulation(container):
    r = await container.router.agents[A.CUSTOMER_SUPPORT].run(task("When will the money from yesterday's sales be deposited?", Intent.CUSTOMER_ACCOUNT))
    assert "get_settlement_schedule" in r.tools_used
    assert r.data["get_settlement_schedule"]["sales_date"] == "2026-11-05"
    assert "simulated" in r.answer.lower()


async def test_support_agent_llm_plan_validated_against_registry(make_container):
    c = make_container(FakeLLM(planner_tools=["get_terminal_status", "rm_rf_tool"]))
    agent = c.router.agents[A.CUSTOMER_SUPPORT]
    tools, method = await agent.plan_tools("hello")
    assert method == "llm" and tools == ["get_terminal_status"]  # hallucinated tool dropped


async def test_support_agent_unknown_user(container):
    r = await container.router.agents[A.CUSTOMER_SUPPORT].run(task("my sales?", Intent.CUSTOMER_ACCOUNT, user="nobody"))
    assert r.status == "partial" and "couldn't find" in r.answer


async def test_support_agent_template_fallback_when_llm_down(make_container):
    c = make_container(FakeLLM(fail=True))
    r = await c.router.agents[A.CUSTOMER_SUPPORT].run(task("When will yesterday's sales be deposited?", Intent.CUSTOMER_ACCOUNT))
    assert r.status == "partial" and "R$" in r.answer and "simulated" in r.answer


async def test_escalation_agent_creates_ticket(container):
    r = await container.router.agents[A.ESCALATION].run(task("talk to a human", Intent.HUMAN))
    assert r.status == "escalated" and r.data["ticket"]["created"] is True
    stored = container.tickets.get(r.data["ticket"]["ticket_id"])  # persisted, not an in-memory list
    assert stored["status"] == "open" and stored["merchant_id"] == "cliente1988"
    assert container.conversations.get_any(stored["conversation_id"])["status"] == "awaiting_human"


async def test_personality_single_vs_merge(container):
    p = container.router.personality
    one = [AgentResult(agent=A.KNOWLEDGE, answer="only")]
    assert await p.compose("q", one) == ("only", False)
    two = one + [AgentResult(agent=A.CUSTOMER_SUPPORT, answer="data")]
    merged, used = await p.compose("q", two)
    assert used is True and "only" in merged and "data" in merged


def test_clean_answer_strips_meta_and_signoff():
    from app.agents.base import clean_answer
    assert clean_answer("Here is a merged answer:\nyour terminal is offline.\n\nBest regards,\nGetnet") == "Your terminal is offline."
    assert clean_answer("Based on the provided CONTEXT excerpts, Pix is free.") == "Pix is free."
