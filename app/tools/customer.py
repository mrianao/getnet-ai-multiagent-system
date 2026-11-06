"""Customer Support tools over SIMULATED merchant data.

!! All records are synthetic, generated deterministically for the assessment. They are NOT
!! real Getnet customer data. Every payload carries `"simulated": True`.

Dates are generated relative to `today` so that questions like "yesterday's sales" always
resolve to concrete records.
"""
from __future__ import annotations

import random
import zlib
from datetime import date, timedelta
from typing import Any, Callable

SIMULATED_NOTICE = "Simulated data for demonstration purposes — not real Getnet customer information."

# Static, synthetic customer profiles (no real PII).
CUSTOMERS: dict[str, dict[str, Any]] = {
    "cliente1988": {
        "name": "Padaria Bom Pão (fictitious)",
        "plan": "Getnet — standard settlement (D+1 for debit and credit)",
        "settlement_rule": {"debit": 1, "credit": 1, "pix": 0},
        "bank_account": "Santander ag. 0001 c/c ****-7 (simulated)",
        "terminals": [
            {"terminal_id": "T-001", "model": "Get Smart", "connectivity": "Wi-Fi + 4G chip", "status": "online"},
            {"terminal_id": "T-002", "model": "Get Clássica", "connectivity": "4G chip", "status": "offline",
             "last_seen_hours_ago": 5, "last_error": "No network signal (chip registration failed)"},
        ],
    },
    "cliente2024": {
        "name": "Loja Exemplo Moda (fictitious)",
        "plan": "Getnet — receivables advance enabled (D+0 credit)",
        "settlement_rule": {"debit": 1, "credit": 0, "pix": 0},
        "bank_account": "Santander ag. 0002 c/c ****-3 (simulated)",
        "terminals": [{"terminal_id": "T-101", "model": "Get Mini", "connectivity": "Bluetooth + smartphone", "status": "online"}],
    },
}

DECLINE_CODES = {
    "51": "Insufficient funds on the cardholder's account — the cardholder must contact their issuing bank.",
    "05": "Do not honour — generic refusal by the issuer; ask the cardholder to contact their bank or use another card.",
    "54": "Expired card — ask for another card.",
    "55": "Incorrect PIN — ask the cardholder to retry carefully.",
    "57": "Transaction not permitted for this card — try another payment method (e.g. Pix).",
}


def _txns(user_id: str, today: date) -> list[dict]:
    rnd = random.Random(f"{user_id}-{today.isoformat()}")  # deterministic per user/day
    rule = CUSTOMERS[user_id]["settlement_rule"]
    out = []
    for back in range(0, 4):
        d = today - timedelta(days=back)
        for n in range(rnd.randint(3, 5)):
            method = rnd.choice(["debit", "credit", "credit", "pix"])
            amount = round(rnd.uniform(8, 350), 2)
            status = "approved"
            code = None
            if back == 0 and n == 0:
                status, code = "declined", "51"
            elif rnd.random() < 0.1:
                status, code = "declined", rnd.choice(["05", "54", "57"])
            installments = rnd.choice([1, 1, 2, 3]) if method == "credit" else 1
            settle = d + timedelta(days=rule[method])
            out.append({
                "transaction_id": f"TX{d.strftime('%m%d')}{n:02d}{zlib.crc32(user_id.encode()) % 97:02d}",
                "date": d.isoformat(), "method": method, "amount_brl": amount, "installments": installments,
                "status": status, "decline_code": code,
                "decline_reason": DECLINE_CODES.get(code) if code else None,
                "expected_settlement_date": settle.isoformat() if status == "approved" else None,
            })
    return out


def get_customer_profile(user_id: str, today: date | None = None) -> dict:
    c = CUSTOMERS.get(user_id)
    if not c:
        return {"found": False, "user_id": user_id, "simulated": True}
    return {"found": True, "user_id": user_id, "name": c["name"], "plan": c["plan"],
            "bank_account": c["bank_account"], "simulated": True}


def get_transaction_status(user_id: str, today: date | None = None, status: str | None = None,
                           transaction_id: str | None = None, days: int = 2) -> dict:
    """Tool 1 — recent transactions (optionally filtered by status / id)."""
    today = today or date.today()
    if user_id not in CUSTOMERS:
        return {"found": False, "user_id": user_id, "simulated": True}
    tx = [t for t in _txns(user_id, today) if t["date"] >= (today - timedelta(days=days - 1)).isoformat()]
    if transaction_id:
        tx = [t for t in tx if t["transaction_id"] == transaction_id]
    if status:
        tx = [t for t in tx if t["status"] == status]
    return {"found": True, "user_id": user_id, "transactions": tx[:10],
            "declined_count": sum(t["status"] == "declined" for t in tx), "simulated": True, "notice": SIMULATED_NOTICE}


def get_settlement_schedule(user_id: str, today: date | None = None, sales_date: str | None = None) -> dict:
    """Tool 2 — when sales of a given day (default: yesterday) will be / were deposited."""
    today = today or date.today()
    if user_id not in CUSTOMERS:
        return {"found": False, "user_id": user_id, "simulated": True}
    sd = sales_date or (today - timedelta(days=1)).isoformat()
    approved = [t for t in _txns(user_id, today) if t["date"] == sd and t["status"] == "approved"]
    by_date: dict[str, float] = {}
    for t in approved:
        by_date[t["expected_settlement_date"]] = round(by_date.get(t["expected_settlement_date"], 0) + t["amount_brl"], 2)
    schedule = [{"deposit_date": k, "amount_brl": v, "status": "paid" if k < today.isoformat() else
                 ("scheduled_today" if k == today.isoformat() else "scheduled")} for k, v in sorted(by_date.items())]
    return {"found": True, "user_id": user_id, "sales_date": sd, "approved_sales": len(approved),
            "gross_amount_brl": round(sum(t["amount_brl"] for t in approved), 2), "schedule": schedule,
            "bank_account": CUSTOMERS[user_id]["bank_account"], "settlement_rule": CUSTOMERS[user_id]["plan"],
            "simulated": True, "notice": SIMULATED_NOTICE}


def get_terminal_status(user_id: str, today: date | None = None) -> dict:
    """Tool 3 — card machine (POS) health: connectivity and last error."""
    if user_id not in CUSTOMERS:
        return {"found": False, "user_id": user_id, "simulated": True}
    return {"found": True, "user_id": user_id, "terminals": CUSTOMERS[user_id]["terminals"],
            "simulated": True, "notice": SIMULATED_NOTICE}


TOOLS: dict[str, Callable[..., dict]] = {
    "get_transaction_status": get_transaction_status,
    "get_settlement_schedule": get_settlement_schedule,
    "get_terminal_status": get_terminal_status,
}
TOOL_DESCRIPTIONS = {
    "get_transaction_status": "Recent card/Pix transactions of the merchant with approval/decline status and decline reasons.",
    "get_settlement_schedule": "When the merchant's sales of a given day are deposited (settlement / payment schedule).",
    "get_terminal_status": "Status of the merchant's card machines (online/offline, connectivity, last error).",
}
