# Storyboard do vídeo (≈ 7 min 40 s)

> Todas as visuais são **estáticas** (capturas de tela). Nenhuma exige o Ollama ou inferência ao
> vivo durante a gravação. Os tempos assumem narração a ~135 palavras/min. Ajuste-os à duração
> real do áudio gerado pelo TTS (veja `recording-checklist.md`).
>
> **Regra de honestidade:** use somente capturas de execuções reais. Quando a evidência ainda não
> foi capturada, a coluna "Evidência a capturar" diz exatamente o que gravar. Não monte JSON nem
> saídas de terminal à mão.

Legenda das fontes das evidências:
- **[Repo]**: arquivo do repositório no GitHub (sempre disponível).
- **[Dev]**: evidência documentada durante o desenvolvimento (`docs/live-validation.md`, `docs/traceability.md`).
- **[Mac]**: evidência reproduzida por você no seu MacBook.

---

## Seção 1 — Introdução e desafio · 0:00 – 0:52

| # | Tempo | Visual | Arquivo / seção | O que o avaliador deve notar |
|---|---|---|---|---|
| 1.1 | 0:00 – 0:12 | Slide de título: "Getnet — Sistema Multiagente de Suporte · Mariana Nascimento · AI Hardcore Engineer" | criado por você (Keynote/Slides) | Identificação |
| 1.2 | 0:12 – 0:40 | Página do GitHub do repositório, topo do README (título + tabela de capacidades da seção 1) | [Repo] `README.md` §1 *Overview* | Escopo completo: router, knowledge, support, escalation, guardrails, observabilidade |
| 1.3 | 0:40 – 0:52 | Tabela "Core requirements" | [Repo] `docs/traceability.md` (topo) | Cada requisito do PDF tem implementação e verificação |

**Narração:** parágrafo 1 ("Olá, eu sou a Mariana Nascimento…").

## Seção 2 — Arquitetura multiagente · 0:52 – 1:59

| # | Tempo | Visual | Arquivo / seção | O que o avaliador deve notar |
|---|---|---|---|---|
| 2.1 | 0:52 – 1:20 | Diagrama Mermaid de componentes, renderizado no GitHub | [Repo] `README.md` §2 *Architecture* | Input → Guardrails → Router → agentes + tools → Personality → Output (igual ao diagrama do PDF) |
| 2.2 | 1:20 – 1:40 | Trecho de código: `class AgentResult` e `class AgentTask` | [Repo] `app/models/schemas.py` linhas 73–90 | Contrato tipado; campo `context: list[AgentResult]` |
| 2.3 | 1:40 – 1:59 | Diagrama de sequência Mermaid | [Repo] `docs/architecture.md` §4 *Message workflow* | Execução sequencial: CS → Knowledge com contexto → Personality |

**Narração:** parágrafo 2 ("A arquitetura segue o diagrama de referência…").

## Seção 3 — Pipeline RAG e recuperação híbrida · 1:59 – 3:27

| # | Tempo | Visual | Arquivo / seção | O que o avaliador deve notar |
|---|---|---|---|---|
| 3.1 | 1:59 – 2:30 | Diagrama Mermaid do pipeline RAG | [Repo] `docs/architecture.md` §5 *RAG pipeline* | Ingestão (crawl → extract → snapshot → chunk → embed → Chroma) separada da recuperação |
| 3.2 | 2:30 – 2:45 | Lista numerada "RAG pipeline" (passos 1–8) | [Repo] `README.md` §11 | 46 páginas, 195 chunks, embeddings multilíngues, ChromaDB |
| 3.3 | 2:45 – 3:12 | Trecho de código: `Retriever.retrieve` com `fused = rrf([dense_rank, lex_rank])` | [Repo] `app/rag/store.py` linhas 80–130 | Busca densa + BM25 fundidas com RRF |
| 3.4 | 3:12 – 3:27 | Parágrafo "Why hybrid?" | [Repo] `README.md` §11 (final) | O problema real (página de carreiras) que motivou a decisão |

**Narração:** parágrafo 3 ("O Knowledge Agent usa RAG…").

Opcional em 3.3: mostrar também `app/rag/hybrid.py` (`class BM25`, linha 22; `def rrf`, linha 45).

## Seção 4 — Ferramentas de suporte e orquestração · 3:27 – 4:31

| # | Tempo | Visual | Arquivo / seção | O que o avaliador deve notar |
|---|---|---|---|---|
| 4.1 | 3:27 – 3:50 | Trecho de código: registro `TOOLS` e o perfil simulado `cliente1988` com `T-002` offline | [Repo] `app/tools/customer.py` linhas 1–30 e 125–135 | Aviso "SIMULATED" no topo do arquivo; 3 ferramentas registradas |
| 4.2 | 3:50 – 4:10 | **Captura JSON real [Mac]** da pergunta "When will the money from yesterday's sales be deposited?" | sua execução no Mac | `agent: customer_support`, `tools` com `get_settlement_schedule` e `get_transaction_status`, nota "(simulated demo data)" |
| 4.3 | 4:10 – 4:31 | Trecho de código: dicionário `PLANS` e o método `classify` (LLM → fallback por regras) | [Repo] `app/agents/router.py` linhas 29–35 e 72–81 | `TROUBLESHOOTING: [CUSTOMER_SUPPORT, KNOWLEDGE]`; `method: "llm"` ou `"rules"` |

**Narração:** parágrafo 4 ("O Customer Support Agent trata perguntas…").

**Evidência a capturar (4.2):** se você ainda tem a saída do terminal do teste feito no Mac,
faça uma captura de tela dela. Se não tiver, rode novamente com o Ollama ligado **antes** da gravação
(o vídeo usa só a imagem) e capture a saída.
```
curl -s localhost:8000/chat -H 'content-type: application/json' -d '{"message":"When will the money from yesterday'"'"'s sales be deposited?","user_id":"cliente1988"}' | python3 -m json.tool
```
Alternativa sem rodar nada: mostre o cenário 3 em `docs/live-validation.md` no GitHub e
diga na narração que é a evidência do desenvolvimento (ela já faz essa distinção).

## Seção 5 — Tecnologia e decisões de engenharia · 4:31 – 5:35

| # | Tempo | Visual | Arquivo / seção | O que o avaliador deve notar |
|---|---|---|---|---|
| 5.1 | 4:31 – 4:55 | Tabela "Tech stack" | [Repo] `README.md` §4 | Ollama + Llama 3.2 3B, fastembed, ChromaDB, FastAPI; cliente compatível com a API OpenAI |
| 5.2 | 4:55 – 5:10 | Lista de ADRs (títulos ADR-001 a ADR-011) | [Repo] `docs/decisions.md` | Decisões registradas, incluindo não usar LangChain (ADR-002) e LLM local (ADR-003) |
| 5.3 | 5:10 – 5:35 | Trecho de código: padrões e `check_input` | [Repo] `app/services/guardrails.py` linhas 1–80 | Bloqueio de injection/fraude; mascaramento de cartão (Luhn) e CPF |

**Narração:** parágrafo 5 ("Nas decisões de tecnologia…").

Opcional: em 5.3, mostrar o bloco `metadata` (trace_id, route, steps) da sua captura JSON do Mac.

## Seção 6 — Testes e evidências reais · 5:35 – 6:51

| # | Tempo | Visual | Arquivo / seção | O que o avaliador deve notar |
|---|---|---|---|---|
| 6.1 | 5:35 – 5:55 | **Captura real [Mac]** do terminal com `python -m pytest -q` → `74 passed` | sua execução no Mac | 74 testes, offline |
| 6.2 | 5:55 – 6:10 | Trecho de código: `test_multi_agent_plan_propagates_context` e a lista `SCENARIOS` | [Repo] `tests/test_router.py` linha 45; `tests/test_scenarios.py` linhas 10–25 | Teste que verifica a passagem de contexto entre agentes; 10 cenários do PDF |
| 6.3 | 6:10 – 6:30 | Tabela de resultados (10 linhas, todas `success`, rota `llm`) | [Dev] `docs/live-validation.md` (topo) | 10/10 com modelo real. Rotule na tela: "Validação documentada no desenvolvimento" |
| 6.4 | 6:30 – 6:40 | Linha 12 (Docker) da tabela de requisitos | [Dev] `docs/traceability.md` | Build, testes no contêiner, healthcheck e chamada real |
| 6.5 | 6:40 – 6:51 | **Capturas reais [Mac]**: resposta de suporte (HTTP 200) e resposta Get Clássica × Get Smart | sua execução no Mac | Rotule na tela: "Reproduzido no meu MacBook" |

**Narração:** parágrafo 6 ("Agora, as evidências…").

**Evidências a capturar:**
- 6.1: `python -m pytest -q` no Mac. **Não precisa do Ollama** (a suíte usa um LLM falso). Capture a última linha.
- 6.5: as duas respostas que você já obteve no Mac (suporte e Get Clássica × Get Smart). Se não
  tiver as capturas, gere-as novamente antes de gravar, com o Ollama ligado.

## Seção 7 — Limitações, produção e conclusão · 6:51 – 7:39

| # | Tempo | Visual | Arquivo / seção | O que o avaliador deve notar |
|---|---|---|---|---|
| 7.1 | 6:51 – 7:10 | Seção "Reviewer notes" | [Dev] `docs/live-validation.md` (final) | Limitações admitidas com franqueza (artefatos do modelo 3B, latência sem GPU) |
| 7.2 | 7:10 – 7:30 | Tabela "Production readiness & evaluation strategy" | [Repo] `README.md` §17 | Avaliação, tracing, alertas, segurança |
| 7.3 | 7:30 – 7:39 | Slide final: URL do repositório + "Obrigada" | criado por você | Link para o avaliador |

**Narração:** parágrafo 7 ("Por fim, as limitações…").

---

## Resumo das capturas necessárias

| Tipo | Quantidade | Precisa do Ollama? |
|---|---|---|
| Slides (título e encerramento) | 2 | Não |
| Páginas do GitHub (README, docs, diagramas Mermaid) | ~11 | Não |
| Trechos de código no GitHub ou no editor | 6 | Não |
| Terminal `pytest` no Mac | 1 | **Não** |
| Respostas JSON reais do Mac (suporte, produto) | 2 | Só se você precisar gerá-las de novo; use as que já tem |
