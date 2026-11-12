"""Pydantic contracts: public API + inter-agent messages (AgentTask / AgentResult)."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class AgentName(str, Enum):
    ROUTER = "router"
    KNOWLEDGE = "knowledge"
    CUSTOMER_SUPPORT = "customer_support"
    ESCALATION = "escalation"


class Intent(str, Enum):
    PRODUCT_INFO = "product_info"
    GENERAL = "general"
    CUSTOMER_ACCOUNT = "customer_account"
    TROUBLESHOOTING = "troubleshooting"
    HUMAN = "human"
    SMALLTALK = "smalltalk"          # greetings / thanks — answered by the router, no agent, no LLM
    OUT_OF_SCOPE = "out_of_scope"    # tasks the assistant does not perform (creative writing, code…)


Status = Literal["success", "partial", "escalated", "blocked", "error"]


# ---------- Public API ----------
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, examples=["When will the money from yesterday's sales be deposited?"])
    # Original {message, user_id} contract preserved. Identity is established by the bearer token;
    # user_id MUST equal the authenticated merchant (otherwise 403) — it can no longer select identity.
    user_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$", examples=["cliente1988"])
    conversation_id: str | None = Field(None, pattern=r"^[a-f0-9]{32}$",
                                        description="Omit to start a new conversation; reuse the returned id for follow-ups.")

    @field_validator("message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return v.strip()


class Source(BaseModel):
    title: str
    url: str
    type: Literal["rag", "web", "tool"]
    score: float | None = None


class StepTrace(BaseModel):
    agent: AgentName
    status: Status
    latency_ms: int
    tools: list[str] = []
    error: str | None = None
    llm_calls: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


class RouteDecision(BaseModel):
    intent: Intent
    agents: list[AgentName] = Field(default_factory=list)  # empty for smalltalk / out_of_scope
    reason: str = ""
    method: Literal["llm", "rules"] = "rules"


class ChatResponse(BaseModel):
    answer: str
    status: Status
    agent: AgentName
    agents_invoked: list[AgentName]
    sources: list[Source] = []
    metadata: dict[str, Any] = {}
    conversation_id: str | None = None


# ---------- Inter-agent messages ----------
class AgentResult(BaseModel):
    agent: AgentName
    status: Status = "success"
    answer: str = ""
    sources: list[Source] = []
    data: dict[str, Any] = {}
    tools_used: list[str] = []
    confidence: float = 1.0
    latency_ms: int = 0
    error: str | None = None
    usage: dict[str, Any] = {}


class AgentTask(BaseModel):
    trace_id: str
    user_id: str          # authenticated merchant id (from the Principal, never from the request body)
    message: str          # standalone message (follow-ups are rewritten with conversation history)
    intent: Intent
    context: list[AgentResult] = []
    conversation_id: str | None = None
    history: list[dict] = []  # bounded window of prior turns (role, content), already sanitized
