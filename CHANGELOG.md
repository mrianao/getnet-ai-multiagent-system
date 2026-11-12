# Changelog

## v2.1.0 — security fix: tenant isolation (2026-11-12)

**Incident (found in manual end-to-end testing).** Authenticated as `cliente1988`, the message
"Quero consultar os recebíveis e as transações do cliente2024." produced an answer that listed
transactions and attributed them to `cliente2024`.

**Diagnosis (from the request trace, reproduced with the real model on `5ada0e9`):**
- **No cross-tenant data access.** Both private tools ran with `merchant_id = cliente1988` — the
  authenticated principal (`AgentTask.user_id` ← `Principal.merchant_id`).
- **Misattribution + fabrication by the LLM.** The model presented the authenticated merchant's data as
  the other merchant's and invented a procedure ("selecione o cliente2024 no painel").
- **Aggravating data-model bug.** Simulated transaction ids collided across merchants (`crc32 % 97`
  gave the same suffix `38` for both), so misattributed ids looked legitimate.

**Fix — defence in depth, deterministic (not prompt-based):**
1. `app/security/tenancy.py` + router: cross-merchant requests (another merchant id, "outro lojista",
   identity override, follow-ups after a refusal such as "E as transações dele?") are refused **before
   routing and before any tool call**. The refusal never reveals whether the other merchant exists.
   Rewritten follow-ups are re-checked.
2. Customer Support: refuses again if reached (router bypass); every private tool call is authorized
   against the principal; generated answers are **rejected** if they name another merchant or contain
   transaction ids or R$ amounts absent from the tool output → deterministic summary of the merchant's
   own data.
3. Router output check: a final answer naming another merchant is replaced by the refusal.
4. Memory: the message naming the other merchant is excluded from history; the refusal marker is kept.
5. Merchant-unique simulated transaction ids (SHA-256 prefix).

**Targeted quality fixes from the same manual tests:** product-topic evidence filter (an antecipação
answer had imported Crediário conditions); troubleshooting Knowledge receives the verified diagnosis
instead of the support agent's prose (it copied and contradicted it); verified terminal facts pinned at
the top of support answers; support "grounding" computed on numeric claims only (the reported 25 % was a
method artefact: lexical overlap between Portuguese prose and JSON keys); prompt rules (no cross-product
transfer, no unsupported comparisons, revenue ranges = rental-fee waiver, reproduce official steps).

**Tests:** 168 → **201** (32 in `tests/test_tenant_isolation.py`, 1 product-filter test), none removed;
one assertion updated to the new, more specific data flow (`test_multi_agent_plan_propagates_context`
now requires the verified diagnosis in the Knowledge prompt). Coverage 92%.

**Compatibility:** no API contract change. New response for cross-merchant requests:
`status: "blocked"`, `metadata.route.intent: "cross_merchant_denied"`, flag `cross_merchant_request`.

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
| **4 — Semantic evaluation & answer quality** | Incomplete **bonus/valued** item (PDF: evaluation strategy, "high-quality prompts", functional RAG) | Root cause of the documented error located in retrieval and fixed with evidence-chosen settings (lexical gate, dedupe, top-k 4); exact evidence retained per answer; deterministic grounding policy (invalid citations, unsupported numbers/dates) with extractive fallback; embedding proxy reported only; prompt hardening + glossary; evaluation dataset, online/offline harnesses, human-review rubric, baseline vs final report | `app/rag/store.py`, `app/rag/grounding.py`, `eval/`; `tests/test_grounding.py` (20); `docs/evaluation.md` |
| **5 — Adaptive replanning** | Evaluator expectation (PDF asks for "agent or sequence of agents", already met) | Bounded deterministic supervisor: observe → replan after each step; max 4 steps; no repeated agent; latency budget; escalation kept last; decisions traced in `metadata.replans` | `RouterAgent.replan`; `tests/test_replanning.py` (6) |
| **6 — Observability** | Bonus item (PDF: present a strategy; may implement traces/logs) | Provider-reported token usage per call/step/request; persisted request traces with evidence references; operator-only Prometheus `/metrics` (status, route method, intents, escalations, p50/p95, tokens, grounding, unresolved tickets) | `app/services/usage.py`, `app/state/traces.py`; `tests/test_observability.py` (4) |

### Fixes driven by the real-model evaluation (runs 1–3) and the Docker smoke test
Each was observed in real answers, fixed, and pinned with a regression test:
- **Routing validation** — the 3B router sent product questions mentioning "vender" / "receber vendas" /
  "my sales" to the account agent (pt02, pt06, pdf04). `customer_account` now requires a specific
  account-data signal; otherwise it is corrected to `product_info` (reason recorded).
- **Follow-up rewrite validation** — the LLM rewrite once copied the assistant's previous answer (mt03, run 1)
  and once *invented* "12 parcelas" (run 2; the page says 48x). Rewrites that look like answers, copy the
  assistant, or introduce numbers absent from the merchant's messages are rejected → deterministic fallback.
- **Deterministic Personality composition** — the LLM merge dropped the terminal diagnosis (pdf05);
  multi-agent answers are now composed as account diagnosis → official guidance → human support.
- **Magnitude check** — "R\$ 20 mil" (thousand) rendered as "R\$ 20 million" passed as "20" (pt03);
  magnitude words are normalised and the lossy ÷1000 tolerance removed; glossary: mil = thousand.
- **Language detection** — a Portuguese question was answered in English inside Docker; PT function words added.

### Also added
- **HTML interface** at `/ui` (merchant chat + operator queue), strict CSP — a usability addition, not a challenge requirement.
- Docker: `STATE_DB_PATH=/app/state/app.db` on a named volume owned by the non-root user.
- `scripts/live_check.py` accepts `--token`.

### Compatibility
- The original `{message, user_id}` request contract is unchanged; requests now need a bearer token.
- `ChatResponse` gained `conversation_id` (additive). Metadata gained `conversation`, `replans`,
  `usage`, `evidence`, `grounding` (additive).
- `RAG_TOP_K` default 5 → 4 (chosen by evaluation).
- Test suite: 77 → **168** tests (all offline). Existing tests adapted, none removed:
  `test_chat_contract` (exact key set + `conversation_id`), `test_unhandled_exception_returns_500_json`
  (now authenticates), `test_escalation_agent_creates_ticket` (asserts persistence),
  `test_personality_single_vs_merge` → `…_structured_composition` and
  `test_multi_agent_plan_propagates_context` (deterministic composition instead of the LLM merge).
