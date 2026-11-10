"""Persistent conversations. Every read is scoped by merchant_id (tenant isolation in SQL)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.state.db import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    def __init__(self, db: Database):
        self.db = db

    def create(self, merchant_id: str, created_by: str) -> dict:
        conv = {"id": uuid.uuid4().hex, "merchant_id": merchant_id, "created_by": created_by, "status": "active",
                "title": None, "created_at": _now(), "updated_at": _now()}
        with self.db.tx() as c:
            c.execute("INSERT INTO conversations (id, merchant_id, created_by, status, title, created_at, updated_at) "
                      "VALUES (:id, :merchant_id, :created_by, :status, :title, :created_at, :updated_at)", conv)
        return conv

    def get(self, conversation_id: str, merchant_id: str) -> dict | None:
        """Owner-scoped lookup: another tenant's id is indistinguishable from a missing one."""
        return self.db.one("SELECT * FROM conversations WHERE id = ? AND merchant_id = ?", (conversation_id, merchant_id))

    def get_any(self, conversation_id: str) -> dict | None:
        """Unscoped lookup — for operator workflows only (callers must enforce the operator role)."""
        return self.db.one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))

    def list(self, merchant_id: str, limit: int = 50) -> list[dict]:
        return self.db.query(
            "SELECT c.id, c.title, c.status, c.created_at, c.updated_at, "
            "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count "
            "FROM conversations c WHERE c.merchant_id = ? ORDER BY c.updated_at DESC LIMIT ?", (merchant_id, limit))

    def add_turn(self, conversation_id: str, messages: list[dict[str, Any]], title: str | None = None) -> None:
        """Append messages atomically (user + assistant of one turn are committed together)."""
        now = _now()
        with self.db.tx() as c:
            for m in messages:
                c.execute("INSERT INTO messages (conversation_id, role, content, trace_id, intent, metadata_json, "
                          "excluded, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (conversation_id, m["role"], m["content"], m.get("trace_id"), m.get("intent"),
                           json.dumps(m.get("metadata"), ensure_ascii=False) if m.get("metadata") else None,
                           int(bool(m.get("excluded"))), now))
            c.execute("UPDATE conversations SET updated_at = ?, title = COALESCE(title, ?) WHERE id = ?",
                      (now, title, conversation_id))

    def set_status(self, conversation_id: str, status: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE conversations SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), conversation_id))

    def messages(self, conversation_id: str) -> list[dict]:
        rows = self.db.query("SELECT id, role, content, trace_id, intent, metadata_json, excluded, created_at "
                             "FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,))
        for r in rows:
            r["metadata"] = json.loads(r.pop("metadata_json")) if r.get("metadata_json") else None
            r["excluded"] = bool(r["excluded"])
        return rows

    def window(self, conversation_id: str, n: int) -> list[dict]:
        """Last `n` messages usable as LLM context (excluded/blocked messages are never replayed)."""
        rows = self.db.query("SELECT role, content, intent FROM messages WHERE conversation_id = ? AND excluded = 0 "
                             "ORDER BY id DESC LIMIT ?", (conversation_id, n))
        return list(reversed(rows))
