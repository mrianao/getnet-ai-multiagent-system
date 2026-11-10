"""Escalation Agent (4th agent) — opens a persistent human-in-the-loop ticket.

The ticket carries the request, the recent (sanitized) conversation, what each agent found and,
when an agent produced an answer, an AI DRAFT that an operator must approve, edit or reject
before the merchant sees it. See app/state/tickets.py for the workflow.
"""
from __future__ import annotations

from app.agents.base import BaseAgent
from app.models.schemas import AgentName, AgentResult, AgentTask
from app.state.conversations import ConversationStore
from app.state.tickets import TicketStore


class EscalationAgent(BaseAgent):
    name = AgentName.ESCALATION

    def __init__(self, tickets: TicketStore, conversations: ConversationStore):
        self.tickets, self.conversations = tickets, conversations

    async def _run(self, task: AgentTask) -> AgentResult:
        failed = [r for r in task.context if r.status == "error"]
        reason = ("agent failure: " + "; ".join(f"{r.agent.value}: {r.error}" for r in failed)) if failed else \
            "merchant requested a human"
        drafts = [r.answer for r in task.context if r.answer and r.status in ("success", "partial")]
        conversation_id = task.conversation_id
        if conversation_id is None:  # direct router use (no chat session): bind the ticket to a new conversation
            conversation_id = self.conversations.create(task.user_id, "escalation-agent")["id"]
        context = {"recent_messages": task.history[-6:],
                   "agent_summaries": [{"agent": r.agent.value, "status": r.status, "answer": r.answer[:500],
                                        "error": r.error} for r in task.context]}
        ticket, created = self.tickets.open_or_get(
            conversation_id=conversation_id, merchant_id=task.user_id, reason=reason, request_message=task.message,
            ai_draft="\n\n".join(drafts) or None, context=context, trace_id=task.trace_id)
        verb = "forwarded your request to" if created else "added this to your open request with"
        return AgentResult(
            agent=self.name, status="escalated", tools_used=["create_handoff_ticket"],
            data={"ticket": {"ticket_id": ticket["id"], "status": ticket["status"], "created": created},
                  "evidence": [{"type": "ticket", "ticket_id": ticket["id"], "created": created}]},
            answer=(f"I've {verb} a Getnet specialist (protocol {ticket['id']}). They will see this conversation, so you "
                    "won't need to repeat yourself; their reply will appear here. "
                    "(Demo: tickets go to the local operator console.)"),
        )
