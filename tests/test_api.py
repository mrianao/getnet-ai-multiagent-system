"""HTTP contract: validation, schema, health, error handling."""
import pytest

from app.models.schemas import AgentName


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["vector_store"]["chunks"] > 100


def test_chat_contract(client):
    r = client.post("/chat", json={"message": "When will the money from yesterday's sales be deposited?", "user_id": "cliente1988"})
    assert r.status_code == 200
    body = r.json()
    # v1 keys are all preserved; v2 adds conversation_id (additive, backward compatible)
    assert set(body) == {"answer", "status", "agent", "agents_invoked", "sources", "metadata", "conversation_id"}
    assert len(body["conversation_id"]) == 32
    assert body["agent"] == "customer_support" and body["status"] == "success"
    for key in ("trace_id", "route", "steps", "latency_ms", "llm_model", "guardrails"):
        assert key in body["metadata"]


@pytest.mark.parametrize("payload", [
    {}, {"message": "hi"}, {"user_id": "u1"}, {"message": "", "user_id": "u1"}, {"message": "   ", "user_id": "u1"},
    {"message": "hi", "user_id": "bad id!"}, {"message": "x" * 2001, "user_id": "u1"}, {"message": 123, "user_id": "u1"},
])
def test_invalid_requests_return_422(client, payload):
    assert client.post("/chat", json=payload).status_code == 422


def test_malformed_json_returns_422(client):
    assert client.post("/chat", content="{not json", headers={"content-type": "application/json"}).status_code == 422


def test_unhandled_exception_returns_500_json(container, merchant_token):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import bearer

    async def explode(message, user_id, **kwargs):
        raise RuntimeError("boom")
    container.router.handle = explode
    with TestClient(create_app(container), raise_server_exceptions=False, headers=bearer(merchant_token)) as c:
        r = c.post("/chat", json={"message": "hi", "user_id": "cliente1988"})
    assert r.status_code == 500 and r.json() == {"status": "error", "detail": "Internal server error"}
