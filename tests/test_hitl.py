"""Stage 6 — operational human-in-the-loop: escalate → claim → resolve → conversation resumes."""
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import bearer

U = "cliente1988"


def _clients(container):
    app = create_app(container)
    m = TestClient(app, headers=bearer(container.tokens.issue("m", "merchant", U)[0]))
    op = TestClient(app, headers=bearer(container.tokens.issue("op-ana", "operator")[0]))
    op2 = TestClient(app, headers=bearer(container.tokens.issue("op-bia", "operator")[0]))
    return m, op, op2


def _escalate(m, text="I want to talk to a human attendant"):
    r = m.post("/chat", json={"message": text, "user_id": U}).json()
    assert r["status"] == "escalated"
    return r["conversation_id"], r["metadata"]["handoff_ticket"]


def test_full_lifecycle_and_conversation_resume(container):
    m, op, op2 = _clients(container)
    with m, op, op2:
        cid, tid = _escalate(m)
        assert m.get(f"/conversations/{cid}").json()["conversation"]["status"] == "awaiting_human"
        # merchant writes while waiting: agents do NOT answer, message is attached to the ticket
        held = m.post("/chat", json={"message": "Any news?", "user_id": U, "conversation_id": cid}).json()
        assert held["metadata"]["held_for_human"] is True and held["agents_invoked"] == []
        queue = op.get("/operator/tickets?status=open").json()["tickets"]
        assert [t["id"] for t in queue] == [tid] and queue[0]["context"]["merchant_followups"][0]["message"] == "Any news?"
        # atomic claim: second operator gets 409; same operator re-claim is idempotent
        assert op.post(f"/operator/tickets/{tid}/claim").status_code == 200
        assert op2.post(f"/operator/tickets/{tid}/claim").status_code == 409
        assert op.post(f"/operator/tickets/{tid}/claim").status_code == 200
        # only the claiming operator may resolve
        assert op2.post(f"/operator/tickets/{tid}/resolve", json={"decision": "reply", "reply": "x"}).status_code == 409
        r = op.post(f"/operator/tickets/{tid}/resolve", json={"decision": "reply", "reply": "Seu repasse cai amanhã."})
        assert r.status_code == 200 and r.json()["status"] == "resolved"
        conv = m.get(f"/conversations/{cid}").json()
        assert conv["conversation"]["status"] == "active"
        assert conv["messages"][-1]["role"] == "operator" and conv["messages"][-1]["content"] == "Seu repasse cai amanhã."
        # workflow continues: next merchant turn is answered by agents again, with the human reply in history
        nxt = m.post("/chat", json={"message": "When will the money from yesterday's sales be deposited?", "user_id": U,
                                    "conversation_id": cid}).json()
        assert nxt["agents_invoked"] == ["customer_support"]
        assert any(h["role"] == "operator" for h in container.conversations.window(cid, 10))


def test_approve_ai_draft_requires_a_draft(container):
    m, op, _ = _clients(container)
    with m, op:
        _, tid = _escalate(m)
        op.post(f"/operator/tickets/{tid}/claim")
        r = op.post(f"/operator/tickets/{tid}/resolve", json={"decision": "approve_draft"})
        assert r.status_code == 422  # explicit human request has no AI draft


async def test_agent_failure_ticket_carries_ai_draft_and_can_be_approved(container):
    from app.models.schemas import AgentName

    async def boom(task):
        raise RuntimeError("tool outage")
    container.router.agents[AgentName.CUSTOMER_SUPPORT]._run = boom
    m, op, _ = _clients(container)
    with m, op:
        r = m.post("/chat", json={"message": "My card machine won't connect to the internet", "user_id": U}).json()
        tid = r["metadata"]["handoff_ticket"]
        t = op.get(f"/operator/tickets/{tid}").json()
        assert "agent failure" in t["reason"] and "tool outage" in t["reason"]
        assert t["ai_draft"]  # knowledge still answered → draft for operator approval
        op.post(f"/operator/tickets/{tid}/claim")
        res = op.post(f"/operator/tickets/{tid}/resolve", json={"decision": "approve_draft"}).json()
        assert res["decision"] == "approve_draft" and res["operator_reply"] == t["ai_draft"]


def test_reject_requires_reason_and_closes(container):
    m, op, _ = _clients(container)
    with m, op:
        cid, tid = _escalate(m)
        op.post(f"/operator/tickets/{tid}/claim")
        assert op.post(f"/operator/tickets/{tid}/resolve", json={"decision": "reject"}).status_code == 422
        r = op.post(f"/operator/tickets/{tid}/resolve", json={"decision": "reject", "reply": "Duplicate request."}).json()
        assert r["status"] == "rejected"
        assert m.get(f"/conversations/{cid}").json()["conversation"]["status"] == "active"


def test_resolve_idempotency_and_no_double_processing(container):
    m, op, _ = _clients(container)
    with m, op:
        cid, tid = _escalate(m)
        op.post(f"/operator/tickets/{tid}/claim")
        body, key = {"decision": "reply", "reply": "Done."}, {"Idempotency-Key": "k-123"}
        assert op.post(f"/operator/tickets/{tid}/resolve", json=body, headers=key).status_code == 200
        assert op.post(f"/operator/tickets/{tid}/resolve", json=body, headers=key).status_code == 200  # safe retry
        assert op.post(f"/operator/tickets/{tid}/resolve", json=body).status_code == 409  # new action on closed ticket
        ops = [x for x in m.get(f"/conversations/{cid}").json()["messages"] if x["role"] == "operator"]
        assert len(ops) == 1  # the reply was delivered exactly once


def test_one_open_ticket_per_conversation(container):
    m, op, _ = _clients(container)
    with m, op:
        cid, tid = _escalate(m)
        # a second escalation in the same conversation (via router directly) reuses the open ticket
        container.conversations.set_status(cid, "active")
        r = m.post("/chat", json={"message": "I want to talk to a human attendant", "user_id": U, "conversation_id": cid}).json()
        assert r["metadata"]["handoff_ticket"] == tid
        assert len(op.get("/operator/tickets?status=open").json()["tickets"]) == 1


def test_role_separation(container):
    m, op, _ = _clients(container)
    with m, op:
        _, tid = _escalate(m)
        assert m.get("/operator/tickets").status_code == 403
        assert m.post(f"/operator/tickets/{tid}/claim").status_code == 403
        assert op.post("/chat", json={"message": "hi", "user_id": U}).status_code == 403


def test_operator_reply_pii_is_masked(container):
    m, op, _ = _clients(container)
    with m, op:
        cid, tid = _escalate(m)
        op.post(f"/operator/tickets/{tid}/claim")
        op.post(f"/operator/tickets/{tid}/resolve", json={"decision": "reply", "reply": "Card 4111 1111 1111 1111 ok"})
        assert "4111 1111 1111 1111" not in str(container.db.query("SELECT * FROM messages"))


def test_tickets_survive_restart(settings):
    import httpx

    from app.container import build_container
    from app.rag.store import VectorStore
    from app.state.db import Database
    from tests.conftest import FakeLLM, HashEmbedder, mock_handler

    def boot():
        return build_container(settings, llm=FakeLLM(), http=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)),
                               store=VectorStore(None, "hitl_restart", HashEmbedder()), db=Database(settings.state_db_path))
    c1 = boot()
    m, op, _ = _clients(c1)
    with m:
        _, tid = _escalate(m)
    c1.db.close()
    c2 = boot()
    op_tok = c2.tokens.issue("op", "operator")[0]
    with TestClient(create_app(c2), headers=bearer(op_tok)) as op:
        assert op.get(f"/operator/tickets/{tid}").json()["status"] == "open"
        assert op.post(f"/operator/tickets/{tid}/claim").status_code == 200


def test_ui_is_served_with_security_headers(anon_client):
    r = anon_client.get("/ui/")
    assert r.status_code == 200 and "Assistente Getnet" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"
    assert anon_client.get("/ui/app.js").status_code == 200
    assert anon_client.get("/", follow_redirects=False).headers["location"] == "/ui/"
    assert "gnt_" not in anon_client.get("/ui/app.js").text.replace('"gnt_…"', "")  # no hardcoded credentials
