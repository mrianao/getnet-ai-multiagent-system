"""Tenant isolation: the authenticated principal is the ONLY source of merchant identity.

Incident (2026-11-12, manual E2E test): authenticated as `cliente1988`, the message "Quero consultar
os recebíveis e as transações do cliente2024." produced an answer that attributed records to
`cliente2024`. The trace showed the tools ran for `cliente1988` (no cross-tenant data access), but
the LLM (a) labelled the authenticated merchant's data as the other merchant's and (b) invented a
"select cliente2024 in the dashboard" procedure. Simulated transaction ids also collided across
merchants, which made the misattribution look plausible.

Defence in depth implemented here and used by the router, the support agent and the chat service:
1. `cross_merchant_reason` — refuse explicit references to another merchant BEFORE any routing or
   tool call (also catches identity-override attempts such as "I am cliente2024" and follow-ups
   like "E as transações dele?" right after a refusal). The refusal never reveals whether the other
   merchant exists.
2. `authorize_tool_call` — every private financial tool call must target the principal's merchant.
3. `foreign_merchant_refs` on generated text — an answer that names another merchant is discarded.
"""
from __future__ import annotations

import re

# Identifier-like references to a merchant: "cliente2024", "merchant 2024", "lojista: abc123", "loja #4410"
_REF = re.compile(
    r"\b(?:cliente|client|customer|merchant|lojista|estabelecimento|loja|store|seller|vendedor)[\s_#:\-]*"
    r"([a-z]{0,12}\d{3,}[a-z0-9]*)\b", re.I)
_BARE_ID = re.compile(r"\b(cliente\d{2,}|client\d{2,}|merchant\d{2,})\b", re.I)
_OTHER = re.compile(
    r"\b(outr[oa]s?\s+(lojistas?|clientes?|estabelecimentos?|lojas?|contas?\s+de\s+(outro|outra))|"
    r"another\s+(merchant|customer|store|seller|account)|other\s+(merchants?|customers?|stores?|sellers?)|"
    r"(merchant|customer|store)\s+other\s+than\s+me|de\s+terceiros|someone\s+else'?s)\b", re.I)
_THIRD_PARTY = re.compile(r"\b(dele|dela|deles|delas|desse cliente|dessa loja|desse lojista|his|her|their|theirs|"
                          r"that merchant|that customer|that store)\b", re.I)

CROSS_MERCHANT_INTENT = "cross_merchant_denied"
REFUSAL_PT = ("Não posso consultar informações financeiras de outro estabelecimento. Você pode consultar apenas os "
              "dados vinculados à sua conta autenticada.")
REFUSAL_EN = ("I can't look up another merchant's financial information. You can only access the data linked to "
              "your authenticated account.")


class TenantViolation(PermissionError):
    """A private tool was about to run for a merchant other than the authenticated principal."""


def _norm(s: str) -> str:
    return s.strip().lower()


def _canonical(ref: str) -> str:
    digits = re.sub(r"\D", "", ref)
    return digits if len(digits) >= 3 else ref.strip().lower()


def foreign_merchant_refs(text: str, own_merchant_id: str | None) -> list[str]:
    """Merchant identifiers referenced in `text` that are NOT the authenticated merchant.

    Identifiers are compared on their canonical form (their digits), so "cliente2024", "merchant 2024"
    and "lojista: 2024" are the same reference, and "lojista 1988" is the authenticated "cliente1988".
    """
    own = _canonical(own_merchant_id or "") if own_merchant_id else None
    refs = set(_REF.findall(text or "")) | set(_BARE_ID.findall(text or ""))
    return sorted({_canonical(r) for r in refs} - {own})


def cross_merchant_reason(message: str, own_merchant_id: str | None, history: list[dict] | None = None) -> str | None:
    """Why this request must be refused as a cross-merchant data request, or None."""
    if foreign_merchant_refs(message, own_merchant_id):
        return "message references another merchant identifier"
    if _OTHER.search(message or ""):
        return "message asks for another merchant's data"
    recent = (history or [])[-2:]
    if any(h.get("intent") == CROSS_MERCHANT_INTENT for h in recent) and _THIRD_PARTY.search(message or ""):
        return "follow-up refers to the merchant of a previously refused request"
    return None


def refusal(message: str) -> str:
    from app.agents.base import detect_language

    return REFUSAL_PT if detect_language(message) == "Portuguese" else REFUSAL_EN


def authorize_tool_call(principal_merchant_id: str | None, target_merchant_id: str | None) -> None:
    if not principal_merchant_id or target_merchant_id != principal_merchant_id:
        raise TenantViolation("private tool call for a merchant other than the authenticated principal")
