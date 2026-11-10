"""Chat use-case: authenticated principal → owned conversation → router → persisted turn."""
from __future__ import annotations

from fastapi import HTTPException, status

from app.models.schemas import AgentName, ChatRequest, ChatResponse
from app.security.auth import Principal
from app.services.guardrails import check_input

BLOCKED_PLACEHOLDER = "[message blocked by guardrails]"


class ChatService:
    def __init__(self, router, conversations, history_window: int = 6, tickets=None, traces=None):
        self.router = router
        self.conversations = conversations
        self.history_window = history_window
        self.tickets = tickets
        self.traces = traces

    def _conversation(self, req: ChatRequest, principal: Principal) -> dict:
        if req.conversation_id:
            conv = self.conversations.get(req.conversation_id, principal.merchant_id)
            if conv is None:  # missing OR owned by another tenant: same answer, no enumeration
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
            return conv
        return self.conversations.create(principal.merchant_id, principal.subject)

    async def handle(self, req: ChatRequest, principal: Principal) -> ChatResponse:
        if req.user_id != principal.merchant_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="user_id does not match the authenticated merchant")
        conv = self._conversation(req, principal)
        guard = check_input(req.message)
        if conv["status"] == "awaiting_human" and self.tickets and guard.allowed:
            resp = self._hold_for_human(conv, guard.sanitized)
        else:
            history = self.conversations.window(conv["id"], self.history_window)
            resp = await self.router.handle(req.message, principal.merchant_id, history=history, conversation_id=conv["id"])
        blocked = resp.status == "blocked"
        user_content = BLOCKED_PLACEHOLDER if blocked else guard.sanitized  # PII masked before persistence
        trace_id = resp.metadata.get("trace_id")
        intent = (resp.metadata.get("route") or {}).get("intent")
        self.conversations.add_turn(conv["id"], [
            {"role": "user", "content": user_content, "trace_id": trace_id, "excluded": blocked},
            {"role": "assistant", "content": resp.answer, "trace_id": trace_id, "intent": intent, "excluded": blocked,
             "metadata": {"status": resp.status, "agents_invoked": [a.value for a in resp.agents_invoked],
                          "sources": [s.model_dump() for s in resp.sources],
                          "handoff_ticket": resp.metadata.get("handoff_ticket")}},
        ], title=None if blocked else user_content[:80])
        resp.conversation_id = conv["id"]
        if self.traces:
            self.traces.record(resp, conv["id"], principal.merchant_id)
        return resp

    def _hold_for_human(self, conv: dict, message: str) -> ChatResponse:
        """While a specialist owns the conversation, agents do not answer (no conflicting replies)."""
        ticket = self.tickets.open_for_conversation(conv["id"])
        if ticket is None:  # stale status (should not happen): fall back to normal processing next turn
            self.conversations.set_status(conv["id"], "active")
            return ChatResponse(answer="Your previous request was closed. Please send your message again.",
                                status="partial", agent=AgentName.ROUTER, agents_invoked=[], metadata={})
        self.tickets.append_merchant_message(ticket["id"], message)
        state = "being handled by" if ticket["status"] == "claimed" else "waiting for"
        return ChatResponse(
            answer=f"Your request {ticket['id']} is {state} a Getnet specialist. I've added this message to it; "
                   "their reply will appear in this conversation.",
            status="escalated", agent=AgentName.ESCALATION, agents_invoked=[],
            metadata={"handoff_ticket": ticket["id"], "ticket_status": ticket["status"], "held_for_human": True,
                      "route": {"intent": "human", "agents": [], "reason": "conversation awaiting human", "method": "rules"}})
