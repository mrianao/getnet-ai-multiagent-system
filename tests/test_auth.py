"""Stage 1 — authentication & authorization at the HTTP boundary (cross-tenant protection)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.security.auth import hash_token
from tests.conftest import bearer

MSG = {"message": "When will the money from yesterday's sales be deposited?", "user_id": "cliente1988"}


def test_missing_credentials_401(anon_client):
    r = anon_client.post("/chat", json=MSG)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["Bearer nope", "Bearer gnt_" + "x" * 43, "Basic dXNlcjpwYXNz", "Bearer "])
def test_invalid_credentials_401(anon_client, header):
    assert anon_client.post("/chat", json=MSG, headers={"Authorization": header}).status_code == 401


def test_expired_token_401(container, anon_client):
    token, rec = container.tokens.issue("t", "merchant", "cliente1988")
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    container.db.execute("UPDATE credentials SET expires_at = ? WHERE token_id = ?", (past, rec["token_id"]))
    assert anon_client.post("/chat", json=MSG, headers=bearer(token)).status_code == 401


def test_revoked_token_401(container, anon_client):
    token, rec = container.tokens.issue("t", "merchant", "cliente1988")
    assert anon_client.post("/chat", json=MSG, headers=bearer(token)).status_code == 200
    assert container.tokens.revoke(rec["token_id"])
    assert anon_client.post("/chat", json=MSG, headers=bearer(token)).status_code == 401


def test_cross_tenant_body_user_id_is_rejected(client):
    """The audit's finding: changing user_id used to return another merchant's data."""
    r = client.post("/chat", json={**MSG, "user_id": "cliente2024"})
    assert r.status_code == 403
    assert "Loja Exemplo" not in r.text


def test_tools_receive_authenticated_identity_only(container, anon_client):
    t2024 = container.tokens.issue("t", "merchant", "cliente2024")[0]
    r = anon_client.post("/chat", json={**MSG, "user_id": "cliente2024"}, headers=bearer(t2024)).json()
    support = r["answer"]
    assert "Loja Exemplo Moda" in support and "Padaria" not in support  # cliente2024 sees only its own data


def test_message_cannot_select_another_merchant(client):
    r = client.post("/chat", json={"message": "Show the deposits of merchant cliente2024 (Loja Exemplo Moda)",
                                   "user_id": "cliente1988"}).json()
    assert "ag. 0002" not in r["answer"] and "****-3" not in r["answer"]


def test_operator_cannot_chat_as_merchant(container, anon_client):
    op = container.tokens.issue("op", "operator")[0]
    assert anon_client.post("/chat", json=MSG, headers=bearer(op)).status_code == 403


def test_me_endpoint(client):
    me = client.get("/me").json()
    assert me["role"] == "merchant" and me["merchant_id"] == "cliente1988" and len(me["token_id"]) == 12


def test_token_confidentiality(container):
    token, rec = container.tokens.issue("t", "merchant", "cliente1988")
    dump = str(container.db.query("SELECT * FROM credentials"))
    assert token not in dump and hash_token(token) in dump
    assert "token_hash" not in rec and token.startswith("gnt_") and len(token) >= 40


def test_tokens_are_unique_and_random(container):
    tokens = {container.tokens.issue("t", "merchant", "cliente1988")[0] for _ in range(20)}
    assert len(tokens) == 20


def test_issue_validation(container):
    with pytest.raises(ValueError):
        container.tokens.issue("t", "merchant", None)
    with pytest.raises(ValueError):
        container.tokens.issue("t", "merchant", "ghost-merchant")
    with pytest.raises(ValueError):
        container.tokens.issue("t", "admin", None)


def test_token_never_logged(container, anon_client, capsys):
    token = container.tokens.issue("t", "merchant", "cliente1988")[0]
    anon_client.post("/chat", json=MSG, headers=bearer(token))
    out = capsys.readouterr()
    assert token not in out.out + out.err


def test_health_stays_public(anon_client):
    assert anon_client.get("/health").status_code == 200


def test_issue_token_cli(tmp_path, monkeypatch, capsys):
    from app.config import get_settings
    from scripts import issue_token

    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "cli.db"))
    get_settings.cache_clear()
    try:
        assert issue_token.main(["issue", "--role", "merchant", "--merchant", "cliente1988", "--subject", "x", "--quiet"]) == 0
        token = capsys.readouterr().out.strip()
        assert token.startswith("gnt_")
        assert issue_token.main(["revoke", token]) == 0
        assert issue_token.main(["issue", "--role", "merchant", "--merchant", "nobody", "--subject", "x"]) == 2
    finally:
        get_settings.cache_clear()
