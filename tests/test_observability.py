"""Stage 8 — token accounting, persisted traces and /metrics."""
import httpx

from app.config import Settings
from app.services import usage
from app.services.llm import OpenAICompatibleLLM
from tests.conftest import bearer


async def test_openai_compatible_client_records_usage():
    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                         "usage": {"prompt_tokens": 42, "completion_tokens": 7}})
    llm = OpenAICompatibleLLM(Settings(), transport=httpx.MockTransport(handler))
    usage.start()
    assert await llm.chat("s", "u") == "ok"
    assert usage.totals() == {"llm_calls": 1, "tokens_in": 42, "tokens_out": 7, "llm_latency_ms": usage.totals()["llm_latency_ms"]}


async def test_usage_unknown_when_provider_does_not_report():
    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    llm = OpenAICompatibleLLM(Settings(), transport=httpx.MockTransport(handler))
    usage.start()
    await llm.chat("s", "u")
    t = usage.totals()
    assert t["llm_calls"] == 1 and t["tokens_in"] is None  # never invented


def test_response_reports_per_step_and_total_usage(client):
    r = client.post("/chat", json={"message": "My card machine won't connect to the internet", "user_id": "cliente1988"}).json()
    steps, total = r["metadata"]["steps"], r["metadata"]["usage"]
    assert all(s["llm_calls"] >= 1 and s["tokens_in"] for s in steps)
    assert total["llm_calls"] >= sum(s["llm_calls"] for s in steps)  # + routing (+ personality merge)
    assert total["tokens_in"] >= sum(s["tokens_in"] for s in steps)


def test_traces_persisted_and_metrics_exposed(container, client, anon_client):
    r = client.post("/chat", json={"message": "How many installments can I split a sale into with the crediário?",
                                   "user_id": "cliente1988"}).json()
    client.post("/chat", json={"message": "I want to talk to a human attendant", "user_id": "cliente1988"})
    t = container.traces.get(r["metadata"]["trace_id"])
    assert t["intent"] == "product_info" and t["latency_ms"] >= 0 and t["detail"]["evidence_refs"]
    assert "text" not in t["detail"]["evidence_refs"][0]  # traces keep references, not full chunk text
    op = bearer(container.tokens.issue("op", "operator")[0])
    body = anon_client.get("/metrics", headers=op).text
    assert 'getnet_requests{status="success"}' in body and "getnet_escalations 1" in body
    assert 'getnet_request_latency_ms{quantile="0.95"}' in body and "getnet_tickets_unresolved 1" in body
    assert anon_client.get(f"/operator/traces/{r['metadata']['trace_id']}", headers=op).status_code == 200
    # metrics and traces are operator-only
    assert client.get("/metrics").status_code == 403
    assert anon_client.get("/metrics").status_code == 401
