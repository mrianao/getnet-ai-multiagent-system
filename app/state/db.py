"""SQLite persistence (Python standard library only).

One file holds credentials, conversations, messages, escalation tickets and request traces.
Chosen over an external database because the workload is a single API process with low write
volume; WAL mode gives durable writes that survive restarts. The schema is created idempotently
at startup (versioned in `schema_version`).
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS credentials (
    token_hash   TEXT PRIMARY KEY,          -- sha256(token); the token itself is never stored
    token_id     TEXT NOT NULL UNIQUE,      -- short public identifier (first 12 hex of the hash)
    subject      TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('merchant', 'operator')),
    merchant_id  TEXT,
    label        TEXT,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    revoked_at   TEXT,
    CHECK ((role = 'merchant' AND merchant_id IS NOT NULL) OR (role = 'operator'))
);

CREATE TABLE IF NOT EXISTS conversations (
    id           TEXT PRIMARY KEY,
    merchant_id  TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'awaiting_human')),
    title        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_conversations_owner ON conversations (merchant_id, updated_at);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT NOT NULL REFERENCES conversations (id),
    role             TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'operator', 'system')),
    content          TEXT NOT NULL,          -- already sanitized (PII masked) before persistence
    trace_id         TEXT,
    intent           TEXT,
    metadata_json    TEXT,
    excluded         INTEGER NOT NULL DEFAULT 0,  -- 1 = never fed back to the LLM (e.g. blocked input)
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_conv ON messages (conversation_id, id);

CREATE TABLE IF NOT EXISTS tickets (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL REFERENCES conversations (id),
    merchant_id      TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('open', 'claimed', 'resolved', 'rejected')),
    reason           TEXT NOT NULL,
    request_message  TEXT NOT NULL,
    ai_draft         TEXT,
    context_json     TEXT,
    claimed_by       TEXT,
    claimed_at       TEXT,
    decision         TEXT CHECK (decision IN ('approve_draft', 'reply', 'reject')),
    operator_reply   TEXT,
    trace_id         TEXT,
    last_action_key  TEXT,             -- Idempotency-Key of the last state transition
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    resolved_at      TEXT,
    version          INTEGER NOT NULL DEFAULT 1
);
-- at most one unresolved ticket per conversation (idempotent escalation)
CREATE UNIQUE INDEX IF NOT EXISTS ux_tickets_one_open ON tickets (conversation_id) WHERE status IN ('open', 'claimed');
CREATE INDEX IF NOT EXISTS ix_tickets_status ON tickets (status, created_at);

CREATE TABLE IF NOT EXISTS request_traces (
    trace_id         TEXT PRIMARY KEY,
    conversation_id  TEXT,
    merchant_id      TEXT,
    created_at       TEXT NOT NULL,
    status           TEXT NOT NULL,
    intent           TEXT,
    route_method     TEXT,
    agents           TEXT,
    latency_ms       INTEGER NOT NULL,
    llm_calls        INTEGER,
    tokens_in        INTEGER,
    tokens_out       INTEGER,
    escalated        INTEGER NOT NULL DEFAULT 0,
    grounding_ratio  REAL,
    grounding_verdict TEXT,
    detail_json      TEXT              -- steps, replans, evidence references
);
CREATE INDEX IF NOT EXISTS ix_traces_created ON request_traces (created_at);
"""


class Database:
    def __init__(self, path: str | None):
        target = path or ":memory:"
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self.path = target
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(target, check_same_thread=False, isolation_level=None, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        if target != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        if not self.one("SELECT version FROM schema_version"):
            self.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Serialized write transaction (BEGIN IMMEDIATE → COMMIT / ROLLBACK)."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
