"""Human-in-the-loop ticket workflow (persistent, atomic, idempotent).

    open ──claim (atomic, one operator)──▶ claimed ──resolve──▶ resolved (approve_draft | reply)
                                                        └──────▶ rejected (reject, with a reason)

Resolution appends the operator's message to the conversation and re-activates it in the SAME
transaction, so the merchant's next turn continues with the human answer in its history.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from app.state.db import Database


class TicketConflict(Exception):
    """Invalid state transition (already claimed by someone else, already resolved, …)."""


class TicketNotFound(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r: dict | None) -> dict | None:
    if r is None:
        return None
    r = dict(r)
    r["context"] = json.loads(r.pop("context_json")) if r.get("context_json") else {}
    r.pop("last_action_key", None)
    return r


class TicketStore:
    def __init__(self, db: Database):
        self.db = db

    def open_or_get(self, *, conversation_id: str, merchant_id: str, reason: str, request_message: str,
                    ai_draft: str | None, context: dict, trace_id: str | None) -> tuple[dict, bool]:
        """Idempotent escalation: at most one unresolved ticket per conversation (unique partial index)."""
        tid = f"GN-{uuid.uuid4().hex[:8].upper()}"
        try:
            with self.db.tx() as c:
                c.execute("INSERT INTO tickets (id, conversation_id, merchant_id, status, reason, request_message, ai_draft, "
                          "context_json, trace_id, created_at, updated_at) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)",
                          (tid, conversation_id, merchant_id, reason, request_message, ai_draft,
                           json.dumps(context, ensure_ascii=False), trace_id, _now(), _now()))
                c.execute("UPDATE conversations SET status = 'awaiting_human', updated_at = ? WHERE id = ?",
                          (_now(), conversation_id))
            return self.get(tid), True
        except sqlite3.IntegrityError:
            return self.open_for_conversation(conversation_id), False

    def open_for_conversation(self, conversation_id: str) -> dict | None:
        return _row(self.db.one("SELECT * FROM tickets WHERE conversation_id = ? AND status IN ('open', 'claimed')",
                                (conversation_id,)))

    def get(self, ticket_id: str) -> dict | None:
        return _row(self.db.one("SELECT * FROM tickets WHERE id = ?", (ticket_id,)))

    def list(self, status: str | None = None, limit: int = 50) -> list[dict]:
        if status:
            rows = self.db.query("SELECT * FROM tickets WHERE status = ? ORDER BY created_at LIMIT ?", (status, limit))
        else:
            rows = self.db.query("SELECT * FROM tickets ORDER BY created_at DESC LIMIT ?", (limit,))
        return [_row(r) for r in rows]

    def append_merchant_message(self, ticket_id: str, message: str) -> None:
        with self.db.tx() as c:
            row = c.execute("SELECT context_json FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            ctx = json.loads(row["context_json"]) if row and row["context_json"] else {}
            ctx.setdefault("merchant_followups", []).append({"at": _now(), "message": message})
            c.execute("UPDATE tickets SET context_json = ?, updated_at = ? WHERE id = ?",
                      (json.dumps(ctx, ensure_ascii=False), _now(), ticket_id))

    def claim(self, ticket_id: str, operator: str, idempotency_key: str | None = None) -> dict:
        with self.db.tx() as c:
            cur = c.execute("UPDATE tickets SET status = 'claimed', claimed_by = ?, claimed_at = ?, updated_at = ?, "
                            "version = version + 1, last_action_key = ? WHERE id = ? AND status = 'open'",
                            (operator, _now(), _now(), idempotency_key, ticket_id))
            if cur.rowcount == 1:
                return _row(dict(c.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()))
            row = c.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            raise TicketNotFound(ticket_id)
        if row["status"] == "claimed" and row["claimed_by"] == operator:
            return _row(dict(row))  # idempotent re-claim by the same operator
        raise TicketConflict(f"ticket is {row['status']}" + (f" by {row['claimed_by']}" if row["claimed_by"] else ""))

    def resolve(self, ticket_id: str, operator: str, decision: str, reply: str | None,
                idempotency_key: str | None = None) -> dict:
        if decision not in ("approve_draft", "reply", "reject"):
            raise ValueError("decision must be approve_draft | reply | reject")
        with self.db.tx() as c:
            row = c.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            if row is None:
                raise TicketNotFound(ticket_id)
            if idempotency_key and row["last_action_key"] == idempotency_key and row["status"] in ("resolved", "rejected"):
                return _row(dict(row))  # retry of an already-applied resolution
            if row["status"] != "claimed" or row["claimed_by"] != operator:
                raise TicketConflict(f"ticket is {row['status']}" + (f" by {row['claimed_by']}" if row["claimed_by"] else "")
                                     + "; claim it before resolving")
            if decision == "approve_draft":
                if not row["ai_draft"]:
                    raise ValueError("this ticket has no AI draft to approve")
                text = row["ai_draft"]
            else:
                if not reply or not reply.strip():
                    raise ValueError("reply text is required for 'reply' and 'reject'")
                text = reply.strip()
            new_status = "rejected" if decision == "reject" else "resolved"
            c.execute("UPDATE tickets SET status = ?, decision = ?, operator_reply = ?, resolved_at = ?, updated_at = ?, "
                      "version = version + 1, last_action_key = ? WHERE id = ? AND status = 'claimed' AND claimed_by = ?",
                      (new_status, decision, text, _now(), _now(), idempotency_key, ticket_id, operator))
            meta = json.dumps({"ticket_id": ticket_id, "decision": decision, "operator": operator}, ensure_ascii=False)
            c.execute("INSERT INTO messages (conversation_id, role, content, metadata_json, excluded, created_at) "
                      "VALUES (?, 'operator', ?, ?, 0, ?)", (row["conversation_id"], text, meta, _now()))
            c.execute("UPDATE conversations SET status = 'active', updated_at = ? WHERE id = ?", (_now(), row["conversation_id"]))
            return _row(dict(c.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()))
