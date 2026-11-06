"""Regression suite: the 10 example scenarios from the assessment PDF (fake LLM → deterministic).

Asserts routing, agents invoked, tools used and source types. Live answers with the real
LLM are validated separately by scripts/live_check.py (see docs/live-validation.md).
"""
import pytest

CS, KN = "customer_support", "knowledge"

SCENARIOS = [
    ("What's the difference between the Get Clássica and the Get Smart?", "product_info", [KN], {"rag_retrieve"}, "rag"),
    ("What's the weather forecast in Porto Alegre tomorrow?", "general", [KN], {"get_weather"}, "web"),
    ("When will the money from yesterday's sales be deposited?", "customer_account", [CS], {"get_settlement_schedule"}, "tool"),
    ("Do I need a bank account to receive my sales via Pix?", "product_info", [KN], {"rag_retrieve"}, "rag"),
    ("My card machine won't connect to the internet, what should I do?", "troubleshooting", [CS, KN], {"get_terminal_status", "rag_retrieve"}, "rag"),
    ("How does receivables advance (antecipação) work with Getnet?", "product_info", [KN], {"rag_retrieve"}, "rag"),
    ("What's the euro exchange rate today?", "general", [KN], {"get_exchange_rate"}, "web"),
    ("My card machine is showing a transaction decline error.", "troubleshooting", [CS, KN], {"get_transaction_status", "rag_retrieve"}, "tool"),
    ("How many installments can I split a sale into with the crediário?", "product_info", [KN], {"rag_retrieve"}, "rag"),
    ("Can I sell through WhatsApp using the Payment Link?", "product_info", [KN], {"rag_retrieve"}, "rag"),
]


@pytest.mark.parametrize("message,intent,agents,tools,source_type", SCENARIOS, ids=[s[0][:40] for s in SCENARIOS])
def test_pdf_scenario(client, message, intent, agents, tools, source_type):
    r = client.post("/chat", json={"message": message, "user_id": "cliente1988"})
    assert r.status_code == 200
    body = r.json()
    assert body["metadata"]["route"]["intent"] == intent
    assert body["agents_invoked"] == agents
    used = {t for step in body["metadata"]["steps"] for t in step["tools"]}
    assert tools <= used, f"expected tools {tools}, got {used}"
    assert body["status"] == "success"
    assert any(s["type"] == source_type for s in body["sources"])
    assert body["answer"]
