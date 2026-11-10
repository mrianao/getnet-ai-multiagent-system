"""Stage 7 — bounded deterministic replanning (observe → replan → next specialist / human)."""
from app.agents.router import MAX_STEPS
from app.models.schemas import AgentName as A
from tests.conftest import FakeLLM


async def test_support_failure_in_troubleshooting_escalates_with_draft(make_container):
    c = make_container()

    async def boom(task):
        raise RuntimeError("tool outage")
    c.router.agents[A.CUSTOMER_SUPPORT]._run = boom
    r = await c.router.handle("My card machine won't connect to the internet", "cliente1988")
    assert r.agents_invoked == [A.CUSTOMER_SUPPORT, A.KNOWLEDGE, A.ESCALATION]  # escalation runs LAST
    assert r.metadata["replans"][0] == {"after": "customer_support", "action": "add escalation",
                                        "reason": "account data unavailable (support agent failed): human follow-up needed"}
    t = c.tickets.get(r.metadata["handoff_ticket"])
    assert t["ai_draft"]  # the knowledge answer is attached for operator approval


async def test_account_question_with_embedded_product_question_adds_knowledge(make_container):
    c = make_container(FakeLLM(router_intent={"deposited": "customer_account"}))
    r = await c.router.handle("When will yesterday's sales be deposited, and how does antecipação work?", "cliente1988")
    assert r.agents_invoked == [A.CUSTOMER_SUPPORT, A.KNOWLEDGE]
    assert r.metadata["replans"][0]["action"] == "add knowledge"


async def test_product_question_with_account_data_reference_adds_support(make_container):
    c = make_container(FakeLLM(router_intent={"antecipação": "product_info"}))
    r = await c.router.handle("How does antecipação work for the sales from yesterday?", "cliente1988")
    assert r.agents_invoked == [A.KNOWLEDGE, A.CUSTOMER_SUPPORT]


async def test_how_to_question_mentioning_my_sales_is_not_replanned(make_container):
    c = make_container()
    r = await c.router.handle("Do I need a bank account to receive my sales via Pix?", "cliente1988")
    assert r.agents_invoked == [A.KNOWLEDGE] and r.metadata["replans"] == []


def test_replan_never_repeats_and_respects_budget(make_container):
    from app.models.schemas import AgentResult, Intent
    c = make_container()
    failed = AgentResult(agent=A.CUSTOMER_SUPPORT, status="error", error="x")
    # escalation already pending → not proposed again
    assert c.router.replan(Intent.TROUBLESHOOTING, "m", failed, [failed], [A.ESCALATION], 0) == []
    ok = AgentResult(agent=A.CUSTOMER_SUPPORT, status="success")
    msg = "When will yesterday's sales be deposited, and how does antecipação work?"
    assert c.router.replan(Intent.CUSTOMER_ACCOUNT, msg, ok, [ok], [], 0)            # within budget: knowledge added
    assert c.router.replan(Intent.CUSTOMER_ACCOUNT, msg, ok, [ok], [], 10_000) == []  # over budget: no extra specialist
    assert MAX_STEPS == 4


async def test_single_agent_scenarios_have_no_replans(make_container):
    c = make_container()
    for q in ["What's the euro exchange rate today?", "How many installments can I split a sale into with the crediário?",
              "When will the money from yesterday's sales be deposited?"]:
        r = await c.router.handle(q, "cliente1988")
        assert r.metadata["replans"] == [], q
