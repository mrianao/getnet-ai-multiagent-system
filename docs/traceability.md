# Requirements Traceability & Compliance Matrix

Source of truth: the assessment PDF. Status values: **PASS** (implemented and verified),
**PARTIAL**, **NOT IMPLEMENTED**.
Verification legend: **T** = automated offline test, **L** = live validation with the real LLM,
real index and real APIs ([`live-validation.md`](live-validation.md)), **D** = Docker run,
**R** = document review.

## Core requirements (PDF)

| # | PDF requirement | Implementation | Verification | Status |
|---|---|---|---|---|
| 1 | ≥ 3 distinct agent types | `app/agents/router.py`, `knowledge.py`, `customer_support.py` (+ `escalation.py`) | T `test_agents.py`, `test_router.py`; L | PASS |
| 2 | Clear communication mechanism between agents | Typed `AgentTask`/`AgentResult` (`app/models/schemas.py`) over direct async calls; context propagation in `RouterAgent.handle` | T `test_multi_agent_plan_propagates_context` (asserts on the actual prompt) | PASS |
| 3 | Router: primary entry point | `POST /chat` → `RouterAgent.handle` (`app/main.py`) | T `test_api.py`; L | PASS |
| 4 | Router decides which agent **or sequence** of agents; manages workflow and data flow | `classify` (LLM JSON + rules fallback), `PLANS` (e.g. troubleshooting = CS → Knowledge), handoff on failure | T `test_router.py` (11 cases), `test_scenarios.py`; L: 10/10 routes correct via `llm` | PASS |
| 5 | Knowledge: Getnet products/services grounded in the company website | RAG over `site.getnet.com.br` (the site linked in the PDF) + `getnet.net/en`, with `sources[]` | T `test_knowledge_rag_answer_has_real_sources`; L scenarios 1, 4, 6, 9, 10 | PASS |
| 6 | Knowledge: RAG approach | `app/rag/*`: crawl → extract → snapshot → chunk → embed (fastembed) → ChromaDB → hybrid retrieval → grounded generation | T `test_rag.py` (incl. retrieval on the real corpus); L | PASS |
| 7 | Knowledge: web search tool for general questions | `app/tools/web.py`: `web_search` (Tavily → DDG Instant Answer → Wikipedia), `get_weather` (Open-Meteo → wttr.in), `get_exchange_rate` (Frankfurter/ECB) | T `test_tools.py`, `test_agents.py`; L scenarios 2, 7 | PASS |
| 8 | Data source `https://www.getnet.net/en` + "look for other sources" | `SEED_URLS` in `app/rag/ingest.py`: getnet.net/en (6 pages) + site.getnet.com.br incl. official FAQ (40 pages) | T `test_snapshot_is_real_getnet_content` | PASS |
| 9 | Customer Support: retrieves relevant user data | `CustomerSupportAgent` (profile lookup by `user_id`, then tools) over **simulated** data | T `test_support_agent_*`; L scenarios 3, 5, 8 | PASS |
| 10 | Customer Support: ≥ 2 tools | 3 tools: `get_transaction_status`, `get_settlement_schedule`, `get_terminal_status` (+ profile) | T `test_tools.py` | PASS |
| 11 | HTTP POST endpoint, payload `{message, user_id}`, meaningful JSON | `POST /chat` → `answer, status, agent, agents_invoked, sources, metadata` | T `test_chat_contract`, 9 invalid-input cases; L; D | PASS |
| 12 | Dockerfile (+ compose) with standard Docker commands | `Dockerfile`, `docker-compose.yml`, `.dockerignore` | D: `docker build` OK; `pytest` inside the image (all tests pass); container `healthy`; real `/chat` through the container | PASS |
| 13 | Testing strategy + approach to integration testing | README §9 + `docs/implementation-plan.md`; 77 tests | T: `pytest` → 77 passed | PASS |
| 14 | Language & frameworks | Python 3.11+, FastAPI, Pydantic, ChromaDB, fastembed (rationale: ADR-001…007) | R | PASS |
| 15 | Spec-Driven Development | `docs/spec.md`, `architecture.md`, `decisions.md`, `implementation-plan.md`, this file | R | PASS |
| 16 | README: build, configure, run, tests | README §6–9 | R | PASS |
| 17 | README: orchestration architecture, design choices, message workflow, RAG (ingestion → storage → retrieval → generation), LLM tool usage | README §2, 3, 11, 12, 13, 15 | R | PASS |
| 18 | "Functional RAG pipeline that ingests the specified URLs" | `python -m app.rag.ingest --crawl` (live crawl, run during development: 46 pages, 195 chunks) | L | PASS |
| 19 | Submission: GitHub repo + video ≤ 15 min | Repo `mrianao/getnet-ai-multiagent-system`; video script `docs/demo-script.md`; email draft `docs/submission-email.md` | Video recording and sending are **manual** | PARTIAL (manual steps pending) |

## Bonus challenges (PDF)

| # | Bonus | Implementation | Verification | Status |
|---|---|---|---|---|
| B1 | Fourth custom agent (Human Escalation) | `app/agents/escalation.py` (ticket `GN-XXXXXXXX`, simulated queue) | T `test_escalation_agent_creates_ticket` | PASS |
| B2 | Guardrails (unsafe, irrelevant, sensitive, unsupported) | `app/services/guardrails.py`: injection and unsafe blocking, card (Luhn)/CPF/CNPJ masking before the LLM | T `test_guardrails.py`, `test_pii_is_masked_before_reaching_llm` | PASS (rules-based; ML moderation is future work) |
| B3 | Human handoff when the system can't confidently resolve | Router: all agents failed, zero retrieval confidence, or the `human` intent → Escalation | T `test_agent_failure_triggers_human_handoff` | PASS |
| B4 | Evaluation & observability strategy | JSON logs with `trace_id`; per-step trace in the response; eval/monitoring plan in README §17 | T (trace fields asserted in `test_chat_contract`); R | PASS (strategy + fundamentals; dashboards not built) |

## The 10 PDF example scenarios

| # | Scenario | Expected route | Offline test | Live (real LLM) |
|---|---|---|---|---|
| 1 | Get Clássica vs Get Smart | Knowledge / RAG | ✅ | ✅ success |
| 2 | Weather in Porto Alegre tomorrow | Knowledge / `get_weather` | ✅ | ✅ success |
| 3 | Yesterday's sales deposit | Customer Support / `get_settlement_schedule` | ✅ | ✅ success |
| 4 | Pix: need a bank account? | Knowledge / RAG | ✅ | ✅ success |
| 5 | Machine won't connect | Customer Support → Knowledge | ✅ | ✅ success |
| 6 | Receivables advance (antecipação) | Knowledge / RAG | ✅ | ✅ success |
| 7 | Euro exchange rate | Knowledge / `get_exchange_rate` | ✅ | ✅ success |
| 8 | Transaction decline error | Customer Support → Knowledge | ✅ | ✅ success |
| 9 | Crediário installments | Knowledge / RAG | ✅ | ✅ success |
| 10 | WhatsApp Payment Link | Knowledge / RAG | ✅ | ✅ success |

**Live run:** 10/10 `success`, 10/10 routed by the LLM (see [`live-validation.md`](live-validation.md)).

## Deviations / notes

- The PDF diagram includes a **Personality** node after the agents. It is implemented as a
  consolidation layer (`app/agents/personality.py`), not as a separate routed agent.
- The PDF calls the company website *"Getnet | Máquinas de cartão e soluções financeiras para o
  seu negócio"* (site.getnet.com.br) and suggests `getnet.net/en`. **Both** are ingested.
- **Time box:** the PDF limits core development to 60 minutes of screen time. This was an
  AI-assisted build. Environment setup (network allow-listing, GitHub access, local LLM download)
  and the live-validation runs took additional wall-clock time beyond the core implementation.
- Customer data is **simulated** (disclosed in every response and in `app/tools/customer.py`).

---

# v2 traceability (remediation, branch `remediation`)

The v1 matrix above is unchanged: it documents that the **original submission already satisfied
every mandatory requirement of the PDF**. This section separates what v2 adds, by origin.
Legend: **T** offline test · **E** real-model evaluation (`docs/evaluation.md`) · **D** Docker run · **R** review.

## A. Original mandatory requirements (PDF) — regression status in v2

| PDF requirement | v2 impact | Verification | Status |
|---|---|---|---|
| ≥ 3 agents + communication mechanism | Unchanged contract; `AgentTask` gained `conversation_id`/`history` (additive) | T `test_router.py`, `test_agents.py` | PASS |
| Router: entry point, agent or sequence, data flow | + follow-up resolution, + bounded replanning | T `test_router.py`, `test_replanning.py`, `test_scenarios.py`; E routing accuracy | PASS |
| Knowledge: RAG grounded in the Getnet website + web search | + retrieval precision, + evidence, + grounding policy | T `test_rag.py`, `test_grounding.py`; E | PASS |
| Customer Support: user data, ≥ 2 tools | Identity now from the authenticated principal | T `test_tools.py`, `test_auth.py`; E | PASS |
| `POST` endpoint with `{message, user_id}` | Same payload; bearer token now required; `user_id` must match | T `test_api.py`, `test_auth.py` | PASS |
| Docker | + state volume, `STATE_DB_PATH` | D (see `docs/evaluation.md` §Docker) | see report |
| Testing strategy | + security, memory, HITL, grounding, replanning, observability suites; + semantic evaluation | T; E | PASS |
| README | Updated for v2 | R | PASS |

## B. Original bonus requirements (PDF)

| Bonus | v1 | v2 | Verification |
|---|---|---|---|
| 4th agent requesting human assistance | PARTIAL (in-memory ticket, no operator side) | **Operational HITL**: persistent queue, claim, approve/reply/reject, resume, idempotency | T `test_hitl.py` (10) |
| Guardrails | PASS (input) | + masking before persistence; blocked input never replayed; rewrite re-checked; no auto-tickets for small talk/out-of-scope | T `test_guardrails.py`, `test_conversations.py`, `test_escalation_behavior.py` |
| Handoff when the system cannot resolve | PARTIAL (also fired on greetings via the rules path) | Escalation only on explicit request, all-agent failure, or replanned support failure | T `test_escalation_behavior.py`, `test_replanning.py` |
| Evaluation & observability strategy | PARTIAL (strategy + logs) | Implemented: dataset + harness + baseline/final report; token accounting, persisted traces, `/metrics` | T `test_observability.py`; E |

## C. Evaluator expectations not stated in the PDF

| Expectation (gap) | v2 implementation | Verification |
|---|---|---|
| Gap 1 — persistent multi-turn memory | SQLite conversations, owner-scoped, follow-up rewrite, bounded window, restart recovery | T `test_conversations.py`; E multi-turn cases |
| Gap 2 — authentication & authorization | Hashed bearer tokens, `Principal`, roles, 401/403/404 matrix | T `test_auth.py`; E `adv04` (403) |
| Gap 4 — semantic correctness measured on evidence | Evidence retained; grounding checks; dataset with facts/forbidden claims; human rubric | T `test_grounding.py`; E |
| Gap 5 — adaptive replanning | Deterministic bounded supervisor | T `test_replanning.py` |

## D. Production-readiness improvements (beyond both)

| Improvement | Status |
|---|---|
| Security headers / CSP on the UI | Implemented (T `test_hitl.py::test_ui_is_served_with_security_headers`) |
| HTML interface (usability, explicitly requested by the author) | Implemented (browser-tested, see `docs/evaluation.md`) |
| IdP (OIDC), rate limiting, OpenTelemetry export, dashboards/alerts, multi-node storage | **Not implemented** — documented as future work |
