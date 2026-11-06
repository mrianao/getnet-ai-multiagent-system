# Specification — Getnet Multi-Agent Support System

> Spec-Driven Development (SDD): this document is the contract. Every requirement has an ID
> (`FR-*`, `NFR-*`), an acceptance criterion, and is traced to code and tests in
> [`traceability.md`](traceability.md). Source of truth: the assessment PDF
> *"AI Hardcore Engineer – Multi-Agent Support System"*.

## 1. Problem statement

Getnet merchants ask a mix of questions: product questions (machines, Pix, Payment Link,
receivables advance), account-specific questions (where is my money? why was this declined?)
and general questions (weather, exchange rate). Build a containerized HTTP service in which
**cooperating agents** interpret each message and produce a useful, grounded answer.

## 2. Scope

**In scope:** Router, Knowledge (RAG + web tools) and Customer Support agents; a Personality
layer that consolidates output (shown in the PDF diagram); an HTTP API; Docker; automated tests;
documentation. Bonus: a Human Escalation agent, guardrails, a handoff mechanism, structured
logs and execution traces.

**Out of scope:** UI, authentication/authorization, real Getnet customer systems, persistent
conversation memory, production deployment.

## 3. Functional requirements

| ID | Requirement (PDF source) | Acceptance criteria |
|----|--------------------------|---------------------|
| FR-01 | ≥ 3 distinct agent types (PDF §1) | `RouterAgent`, `KnowledgeAgent` and `CustomerSupportAgent` exist as separate classes with distinct responsibilities. |
| FR-02 | A clear communication mechanism between agents (PDF §1) | Agents communicate through typed `AgentTask` → `AgentResult` messages via direct async calls. The output of earlier agents is passed as `context` to later agents. |
| FR-03 | The Router is the entry point, analyses the message and decides which agent **or sequence of agents** handles it (PDF §1) | Every `/chat` request enters through `RouterAgent.handle`. The router returns a `RouteDecision` with `agents: list` (length ≥ 1) and a reason. Multi-agent plans run in order, and the response metadata shows each step. |
| FR-04 | The Router manages workflow and data flow between agents (PDF §1) | In multi-agent plans, agent *n+1* receives the results of agents *1..n*. A test asserts that context propagates. |
| FR-05 | The Knowledge Agent answers Getnet product questions grounded in the Getnet website (PDF §1) | Product questions are answered from retrieved chunks. The response includes `sources[]` with real URLs from the ingested corpus. |
| FR-06 | Functional RAG pipeline that ingests the specified URLs, plus other sources (PDF §1, "What we look for") | The pipeline runs ingestion (HTTP fetch of `getnet.net/en` and `site.getnet.com.br`) → text extraction → chunking → embeddings → vector store (ChromaDB) → top-k retrieval → LLM generation. No hardcoded FAQ. |
| FR-07 | Web search tool for general-purpose questions (PDF §1) | General questions (weather, FX, general facts) are answered from live tool calls: a web search tool (Tavily if a key is set, otherwise the DuckDuckGo Instant Answer API, then Wikipedia), plus weather (Open-Meteo) and FX (Frankfurter/ECB). |
| FR-08 | The Customer Support Agent retrieves user data to resolve inquiries (PDF §1) | It uses `user_id` to fetch the customer profile and records. The answer references the user's data. |
| FR-09 | ≥ 2 Customer Support tools (PDF §1) | It implements `get_transaction_status`, `get_settlement_schedule` and `get_terminal_status` (3 tools), each unit-tested. |
| FR-10 | `POST` HTTP endpoint accepting `{message, user_id}` and returning meaningful JSON (PDF §2) | `POST /chat` validates the input (422 on invalid) and returns `answer`, `status`, `agent`, `agents_invoked`, `sources`, `metadata` (trace_id, route, steps, latency). |
| FR-11 | Dockerfile (+ compose) builds and runs with standard commands (PDF §3) | `docker build` and `docker compose up` start the API. `GET /health` returns 200. |
| FR-12 | Testing strategy + integration testing approach (PDF §4) | Pytest suite with unit, integration (full orchestration with a fake LLM) and API tests. The 10 PDF scenarios are covered. The strategy is documented in the README. |
| FR-13 | README covers build/configure/run/test, architecture, design choices, message workflow, RAG (ingestion → storage → retrieval → generation) and LLM tool usage (PDF "README must cover") | Each topic has its own README section. |
| FR-14 | Health endpoint (prompt) | `GET /health` reports status, LLM provider and the vector store's document count. |
| BR-01 | *Bonus:* fourth agent, Human Escalation (PDF Bonus) | `EscalationAgent` creates a (simulated) handoff ticket and returns a protocol number. |
| BR-02 | *Bonus:* guardrails for unsafe, irrelevant, sensitive or unsupported requests (PDF Bonus) | Input guardrail blocks prompt injection and unsafe content, and masks card numbers/CPF. Out-of-scope requests receive a polite refusal. |
| BR-03 | *Bonus:* human handoff when the system cannot confidently resolve (PDF Bonus) | Low retrieval confidence, agent failure or an explicit "talk to a human" request routes to `EscalationAgent`. |
| BR-04 | *Bonus:* evaluation and observability strategy (PDF Bonus) | Structured JSON logs with `trace_id`, per-step timings in the response, and a documented eval/monitoring plan. |

## 4. Non-functional requirements

| ID | Requirement | Acceptance criteria |
|----|-------------|---------------------|
| NFR-01 | Zero paid dependencies | The default stack is Ollama (`llama3.2:3b`) + fastembed (local) + ChromaDB (embedded) + free public APIs. |
| NFR-02 | Runs on a MacBook Pro M1 with 8 GB RAM | A 3B model (~2 GB) plus a multilingual MiniLM embedding model (~0.25 GB). No GPU required. |
| NFR-03 | Provider-agnostic LLM | Any OpenAI-compatible endpoint (Ollama, Groq, OpenAI) can be selected through `.env` only. |
| NFR-04 | Deterministic, offline test suite | Tests use a fake LLM, a hash-based fake embedder and mocked HTTP. No network is needed. |
| NFR-05 | Resilience | A malformed LLM JSON falls back to deterministic rules. A tool or HTTP failure returns a degraded but valid response (`status: "partial"`). Network calls have timeouts. |
| NFR-06 | No secrets or real PII in the repo | `.env` is git-ignored. Mock customer data is synthetic and labelled `"simulated": true`. |
| NFR-07 | Honesty | The system never fabricates sources: `sources[]` only contains URLs that were actually retrieved or returned by tools. |

## 5. Data models (API)

```jsonc
// POST /chat — request
{ "message": "string (1..2000 chars)", "user_id": "string (1..64, [A-Za-z0-9_.-])" }

// 200 — response
{
  "answer": "string",
  "status": "success | partial | escalated | blocked | error",
  "agent": "knowledge | customer_support | escalation | router",      // primary agent
  "agents_invoked": ["customer_support", "knowledge"],
  "sources": [{ "title": "...", "url": "https://...", "type": "rag | web | tool", "score": 0.71 }],
  "metadata": {
    "trace_id": "uuid", "route": { "agents": [...], "intent": "...", "reason": "...", "method": "llm | rules" },
    "steps": [{ "agent": "...", "status": "...", "latency_ms": 123, "tools": ["..."] }],
    "latency_ms": 456, "llm_provider": "ollama", "llm_model": "llama3.2:3b",
    "guardrails": { "pii_masked": false, "flags": [] }
  }
}
```

Errors: `422` validation (FastAPI/Pydantic), `500` → `{"status":"error","detail":...,"trace_id":...}`.

## 6. Routing contract

| Intent | Example | Plan |
|--------|---------|------|
| `product_info` | "Get Clássica vs Get Smart?" | `[knowledge]` (RAG) |
| `general` | "Weather in Porto Alegre", "Euro rate" | `[knowledge]` (web tools) |
| `customer_account` | "When will yesterday's sales be deposited?" | `[customer_support]` |
| `troubleshooting` | "Machine won't connect", "Decline error" | `[customer_support, knowledge]` (user data → grounded how-to) |
| `human` | "I want to talk to a person" | `[escalation]` |
| `unsafe/blocked` | prompt injection, harmful content | guardrail → `blocked` |

## 7. Error handling

- LLM unavailable → routing falls back to rules. The agent then returns a retrieval-only/tool-only
  answer with `status: partial`, and the answer says the LLM was unavailable.
- Web/tool failure → the error is captured in the step, and other agents still answer (`partial`).
- RAG low confidence (best similarity < threshold) → the answer says the information was not found
  and offers a handoff (BR-03).
- Unknown `user_id` → the Customer Support Agent answers that no customer record was found.

## 8. Limitations (accepted)

- `llama3.2:3b` is a small model. Answers are simpler than with frontier models, and JSON routing
  can fail (mitigated by the rules fallback).
- Customer data is simulated. There is no real Getnet integration.
- The corpus is a point-in-time crawl of public pages. Content rendered by JavaScript may be missed.
