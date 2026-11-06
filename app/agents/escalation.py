"""Escalation Agent (bonus 4th agent) — human handoff via a (simulated) ticket queue."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.agents.base import BaseAgent
from app.models.schemas import AgentName, AgentResult, AgentTask

HANDOFF_QUEUE: list[dict] = []  # in production: Service Bus / Zendesk / Teams webhook


class EscalationAgent(BaseAgent):
    name = AgentName.ESCALATION

    async def _run(self, task: AgentTask) -> AgentResult:
        reason = task.context[-1].error or "low confidence" if task.context else "user requested a human"
        ticket = {
            "ticket_id": f"GN-{uuid.uuid4().hex[:8].upper()}",
            "user_id": task.user_id,
            "trace_id": task.trace_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "message": task.message,
            "agent_summaries": [{"agent": r.agent.value, "status": r.status, "answer": r.answer[:300]} for r in task.context],
            "simulated": True,
        }
        HANDOFF_QUEUE.append(ticket)
        return AgentResult(
            agent=self.name, status="escalated", tools_used=["create_handoff_ticket"], data={"ticket": ticket},
            answer=(f"I've forwarded your request to a Getnet human specialist (protocol {ticket['ticket_id']}). "
                    "They will receive the full context of this conversation, so you won't need to repeat yourself. "
                    "(simulated handoff for this demo)"),
        )
