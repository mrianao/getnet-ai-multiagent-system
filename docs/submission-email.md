# Submission email (draft — do not send automatically)

**To:** stefany.morais@getnet.net
**Subject:** AI Hardcore Engineer – Technical Assessment Submission – Mariana Nascimento

---

Hi Stefany,

I hope you're well. Please find below my submission for the **AI Hardcore Engineer –
Multi-Agent Support System** technical assessment.

- **GitHub repository:** `<GITHUB_REPOSITORY_URL>`
- **Video walkthrough (unlisted YouTube):** `<YOUTUBE_VIDEO_URL>`

**Summary of the solution**
- **Multi-agent orchestration:** a Router Agent (LLM intent classification with deterministic
  fallback, multi-step plans with context passing), a Knowledge Agent (hybrid RAG over Getnet's
  official websites plus web tools for general questions), a Customer Support Agent (3 tools over
  simulated merchant data), a bonus Human Escalation Agent, and a Personality layer that
  consolidates the final answer.
- **Fully free and local stack:** Ollama `llama3.2:3b`, multilingual local embeddings and
  ChromaDB. The LLM client is OpenAI-compatible, so it can be switched to cloud providers by
  configuration.
- **FastAPI** `POST /chat` and `GET /health`, **Docker / docker compose**, guardrails (prompt
  injection, unsafe requests, PII masking) and structured tracing.
- **Testing:** an offline automated suite (unit, component, orchestration integration, API
  contract, and the 10 example scenarios as regression tests), plus a separate live validation
  report against the real model.
- Developed with **Spec-Driven Development**: the spec, architecture, ADRs and a requirements
  traceability matrix are in `/docs`.

The repository is currently private. Please let me know which GitHub usernames should be given
access, or if you'd prefer that I make it public.

Thank you for the opportunity. I'm happy to discuss any part of the solution.

Best regards,
Mariana Nascimento
