# Architecture Decision Records

### ADR-001 — Python 3.11+ / FastAPI / Pydantic v2
FastAPI gives typed validation, OpenAPI docs and async support with almost no boilerplate.
Pydantic models are shared between the API contract and the inter-agent messages.

### ADR-002 — No agent framework (LangChain / LlamaIndex / LangGraph)
The PDF allows any library. The orchestration is small (one router, three or four agents,
sequential plans), so a hand-written orchestrator is **easier to read, test and explain**.
It also avoids framework churn and heavy dependencies. Trade-off: no built-in graph
checkpointing. The `AgentTask`/`AgentResult` contract keeps a migration to LangGraph
straightforward if branching or parallel workflows become necessary.

### ADR-003 — Local LLM by default: Ollama + `llama3.2:3b`
Requirement: no paid API, and it must run on a MacBook Pro M1 with 8 GB. `llama3.2:3b` (Q4,
~2 GB) runs at an interactive speed on Apple Silicon and supports JSON mode. Larger 7–8B models
would need ~5 GB and swap on 8 GB machines. Mitigations for a small model: JSON mode plus
Pydantic validation, a rules fallback for routing and tool selection, short focused prompts and
low temperature. **Any OpenAI-compatible endpoint** (Groq free tier, OpenAI, Azure via a gateway)
can be selected through `.env`.

### ADR-004 — Local embeddings: fastembed multilingual MiniLM
Free, CPU/ONNX, ~220 MB, no PyTorch. It is **multilingual**: the main Getnet Brazil site is in
Portuguese, while the PDF's questions are in English. Cross-lingual retrieval handles this
without translation.

### ADR-005 — ChromaDB (embedded, persistent)
A real vector database with metadata, cosine search and persistence on disk, and no extra
container. Embeddings are computed by our own embedder, so the vector store and the embedding
model are decoupled. In production it would be replaced by a managed store (Azure AI Search,
pgvector, Qdrant).

### ADR-006 — Corpus snapshot committed to the repo
Crawling is slow and depends on the network. Committing the extracted public text
(`data/corpus/pages.jsonl`, with the source URL per page) makes the demo and Docker image
reproducible and makes ingestion auditable. `python -m app.rag.ingest --crawl` refreshes it from
the live sites. The index (Chroma) is rebuilt from the snapshot at startup if empty.

### ADR-007 — Web search via free, official APIs
Scraping search-engine HTML conflicts with their bot protections and is fragile. The web-search
tool uses Tavily (free tier, optional key), then the DuckDuckGo Instant Answer JSON API, then
Wikipedia's search API. For the two live-data scenarios in the PDF, there are dedicated tools:
**Open-Meteo** (weather forecast) and **Frankfurter** (ECB FX rates). These are deterministic,
keyless and citable.

### ADR-008 — Hybrid router: LLM-first, rules fallback
The LLM handles phrasing variety. The rules guarantee availability and make tests deterministic.
`metadata.route.method` records which path decided, so the fallback rate can be monitored.

### ADR-009 — Sequential multi-agent plans with context propagation
Troubleshooting needs the user's data first (Customer Support), then grounded how-to steps from
the knowledge base (Knowledge). A sequential plan with explicit context passing is simpler and
cheaper on a CPU-bound local LLM than parallel fan-out.

### ADR-010 — Personality layer: an LLM call only when needed
With one agent, its answer already follows the brand voice (shared system prompt), so
Personality only formats it. This saves one LLM call (~2–4 s on M1). With several agents,
Personality uses the LLM to merge them into one coherent reply.

### ADR-011 — Simulated customer data, generated relative to "today"
Questions like "yesterday's sales" need dates relative to now. Records are generated
deterministically (fixed seed) relative to the current date, flagged `simulated: true`, and every
Customer Support answer states that the data is simulated.
