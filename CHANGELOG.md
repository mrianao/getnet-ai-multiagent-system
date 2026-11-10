# Changelog

## v2.0.0 — remediation after evaluator feedback (branch `remediation`)

The original submission is preserved at tag **`v1.0-submission`** (commit `2f94c47`); `origin/main`
before this work (adds the Anthropic provider, PR #1) is tagged **`v1.1-pre-remediation`**.

The evaluators rated the submission *"Bom, com gaps"* (Critical Security: not triggered; Challenge
Fit: Strong Pass; Evidence: Strong, with reservations; Semantic Quality: Partial). The audit
confirmed that **all mandatory challenge requirements were already met**; the gaps below are
incomplete bonus items, evaluator expectations beyond the PDF, or production-readiness work.
Measured before/after results: [`docs/evaluation.md`](docs/evaluation.md).

| Evaluator gap | Classification (vs. the challenge PDF) | What changed | Evidence |
|---|---|---|---|
| **1 — Persistent multi-turn memory** | Evaluator expectation (not in the PDF) | SQLite conversations/messages; `conversation_id` in request/response; owner-scoped access; bounded 6-message window; deterministic follow-up detection + LLM standalone rewrite validated by guardrails, deterministic fallback; PII masked and blocked inputs excluded before persistence; restart recovery | `app/state/conversations.py`, `app/services/chat.py`, `RouterAgent.resolve_followup`; `tests/test_conversations.py` (13) |
| **2 — Authentication & authorization** | Evaluator expectation + production readiness (not in the PDF); the audit reproduced a cross-tenant read | Bearer tokens (256-bit, SHA-256-hashed in SQLite, expiry, revocation), `Principal` with merchant/operator roles, `user_id` must match the token (403), tools and conversations bound to the principal, `scripts/issue_token.py` | `app/security/auth.py`; `tests/test_auth.py` (18) |
| **3 — Operational human-in-the-loop** | Incomplete **bonus** item (PDF: agent that requests assistance from a human operator; handoff) | Persistent ticket queue; atomic claim (409); approve AI draft / reply / reject; resolution appends the operator reply and resumes the conversation atomically; merchant messages held while waiting; one open ticket per conversation; `Idempotency-Key`; survives restarts; operator API + UI. Fixed unjustified escalations (greetings/out-of-scope/zero hits on the rules path) | `app/state/tickets.py`, `app/agents/escalation.py`; `tests/test_hitl.py` (10), `tests/test_escalation_behavior.py` (13) |
| **4 — Semantic evaluation & answer quality** | Incomplete **bonus/valued** item (PDF: evaluation strategy, "high-quality prompts", functional RAG) | Root cause of the documented error located in retrieval and fixed with evidence-chosen settings (lexical gate, dedupe, top-k 4); exact evidence retained per answer; deterministic grounding policy (invalid citations, unsupported numbers/dates) with extractive fallback; embedding proxy reported only; prompt hardening + glossary; evaluation dataset, online/offline harnesses, human-review rubric, baseline vs final report | `app/rag/store.py`, `app/rag/grounding.py`, `eval/`; `tests/test_grounding.py` (19); `docs/evaluation.md` |
| **5 — Adaptive replanning** | Evaluator expectation (PDF asks for "agent or sequence of agents", already met) | Bounded deterministic supervisor: observe → replan after each step; max 4 steps; no repeated agent; latency budget; escalation kept last; decisions traced in `metadata.replans` | `RouterAgent.replan`; `tests/test_replanning.py` (6) |
| **6 — Observability** | Bonus item (PDF: present a strategy; may implement traces/logs) | Provider-reported token usage per call/step/request; persisted request traces with evidence references; operator-only Prometheus `/metrics` (status, route method, intents, escalations, p50/p95, tokens, grounding, unresolved tickets) | `app/services/usage.py`, `app/state/traces.py`; `tests/test_observability.py` (4) |

### Also added
- **HTML interface** at `/ui` (merchant chat + operator queue), strict CSP — a usability addition, not a challenge requirement.
- Docker: `STATE_DB_PATH=/app/state/app.db` on a named volume owned by the non-root user.
- `scripts/live_check.py` accepts `--token`.

### Compatibility
- The original `{message, user_id}` request contract is unchanged; requests now need a bearer token.
- `ChatResponse` gained `conversation_id` (additive). Metadata gained `conversation`, `replans`,
  `usage`, `evidence`, `grounding` (additive).
- `RAG_TOP_K` default 5 → 4 (chosen by evaluation).
- Two existing tests were adapted for the authenticated contract (`test_chat_contract` key set,
  `test_unhandled_exception_returns_500_json` now authenticates); one was strengthened
  (`test_escalation_agent_creates_ticket` asserts persistence). No test was removed.
