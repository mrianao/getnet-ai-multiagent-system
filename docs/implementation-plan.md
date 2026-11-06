# Implementation Plan

Time-boxed to the PDF's 60-minute core-development budget. P0 is mandatory, P1 is quality and
P2 is bonus.

| # | Step | Priority | Output | Verification |
|---|------|----------|--------|--------------|
| 1 | SDD docs: spec, architecture, decisions, plan | P0 | `docs/*.md` | review |
| 2 | Config, schemas, LLM client, logging | P0 | `app/config.py`, `app/models/`, `app/services/` | unit tests |
| 3 | RAG: crawler/extractor, chunker, embedder, Chroma store, retriever | P0 | `app/rag/` | `tests/test_rag.py` + live ingestion |
| 4 | Web tools: search, weather, FX | P0 | `app/tools/web.py` | mocked-HTTP tests + live call |
| 5 | Customer tools + simulated data | P0 | `app/tools/customer.py` | `tests/test_customer_tools.py` |
| 6 | Agents: Knowledge, Customer Support, Personality | P0 | `app/agents/` | `tests/test_agents.py` |
| 7 | Router + orchestration (plans, context, fallback) | P0 | `app/agents/router.py` | `tests/test_router.py` |
| 8 | API `/chat`, `/health`, error handling | P0 | `app/main.py` | `tests/test_api.py` |
| 9 | 10 PDF scenarios end-to-end (fake LLM) | P0 | `tests/test_scenarios.py` | pytest |
| 10 | Docker + compose + `.env.example` | P0 | `Dockerfile`, `docker-compose.yml` | `docker build` + run |
| 11 | Guardrails + Escalation agent + handoff | P2 | `app/services/guardrails.py`, `app/agents/escalation.py` | tests |
| 12 | README, demo script, submission email, traceability | P0 | docs | review |
| 13 | Live validation with real Ollama + real RAG | P1 | `scripts/live_check.py` output | manual / recorded |
| 14 | Publish to GitHub | P0 | `main` branch | `git ls-remote`, secret scan |

## Testing strategy (summary — full version in README)

1. **Unit:** chunker, guardrails, tool functions, rules router, JSON parsing.
2. **Component:** each agent with a `FakeLLM` (scripted responses) and a fake embedder.
3. **Integration:** the full Router → agents → Personality pipeline over a temporary Chroma
   index built from a fixture corpus, with HTTP mocked by `httpx.MockTransport`.
4. **API contract:** `TestClient` for validation errors, response schema and error handling.
5. **Scenario regression:** the 10 PDF scenarios assert the selected agents, tools used and sources.
6. **Live (opt-in, not in CI):** `scripts/live_check.py` runs the 10 scenarios against the real
   Ollama model and the real index, and prints routes, latency and sources.
