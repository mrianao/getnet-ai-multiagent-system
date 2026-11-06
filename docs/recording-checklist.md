# Checklist de gravação, montagem e envio

## 1. Gerar a narração (TTS)

1. Abra `docs/voiceover-script.md`. Ele tem 7 parágrafos, um por seção.
2. **Recomendado:** gere **um áudio por parágrafo** (7 arquivos: `01.mp3` … `07.mp3`). Assim você
   sabe exatamente quanto tempo cada seção dura, e corrigir uma frase não exige regenerar tudo.
3. Configuração do TTS: voz feminina em **português do Brasil (pt-BR)**, velocidade normal (1.0×),
   formato MP3 ou WAV, 44.1 ou 48 kHz.
4. Ouça os 7 áudios. Confira a pronúncia de: "RAG", "Router Agent", "Knowledge Agent", "AgentTask",
   "AgentResult", "Chroma DB", "BM vinte e cinco", "Reciprocal Rank Fusion", "Llama", "Ollama",
   "FastAPI", "LangChain", "OpenTelemetry". Se algum termo soar mal, troque a grafia só no texto
   enviado ao TTS (ex.: "Lhama" no lugar de "Llama", "Ô-lama" no lugar de "Ollama").
5. Anote a duração de cada áudio. O total esperado é de **7 a 8 minutos**.

## 2. Capturar as visuais (na ordem)

Use **Cmd + Shift + 4** (área) ou **Cmd + Shift + 5**. Use o zoom do navegador em 125–150 %
para o texto ficar legível em 1080p. Nomeie os arquivos na ordem: `01-titulo.png`, `02-readme.png`…

| Ordem | Seção | Visual | Fonte |
|---|---|---|---|
| 01 | 1 | Slide de título | Keynote/Google Slides |
| 02 | 1 | README no GitHub: título + tabela de capacidades (§1) | GitHub |
| 03 | 1 | `docs/traceability.md`: tabela Core requirements | GitHub |
| 04 | 2 | README §2: diagrama Mermaid de arquitetura | GitHub |
| 05 | 2 | `app/models/schemas.py`: `AgentResult` / `AgentTask` (linhas 73–91) | GitHub |
| 06 | 2 | `docs/architecture.md` §4: diagrama de sequência | GitHub |
| 07 | 3 | `docs/architecture.md` §5: diagrama do pipeline RAG | GitHub |
| 08 | 3 | README §11: passos do pipeline RAG | GitHub |
| 09 | 3 | `app/rag/store.py`: `Retriever.retrieve` (linhas 80–130) | GitHub |
| 10 | 3 | README §11: parágrafo "Why hybrid?" | GitHub |
| 11 | 4 | `app/tools/customer.py`: aviso SIMULATED + `TOOLS` | GitHub |
| 12 | 4 | **JSON real do Mac**: resposta de liquidação (suporte) | Seu terminal |
| 13 | 4 | `app/agents/router.py`: `PLANS` + `classify` | GitHub |
| 14 | 5 | README §4: tabela Tech stack | GitHub |
| 15 | 5 | `docs/decisions.md`: títulos dos ADRs | GitHub |
| 16 | 5 | `app/services/guardrails.py` | GitHub |
| 17 | 6 | **Terminal do Mac**: `python -m pytest -q` → `74 passed` | Seu terminal |
| 18 | 6 | `tests/test_router.py` (linha 45) + `tests/test_scenarios.py` | GitHub |
| 19 | 6 | `docs/live-validation.md`: tabela 10/10 (rótulo "Validação do desenvolvimento") | GitHub |
| 20 | 6 | `docs/traceability.md`: linha 12 (Docker) | GitHub |
| 21 | 6 | **JSON real do Mac**: suporte + Get Clássica × Get Smart (rótulo "Reproduzido no meu MacBook") | Seu terminal |
| 22 | 7 | `docs/live-validation.md`: Reviewer notes | GitHub |
| 23 | 7 | README §17: Production readiness | GitHub |
| 24 | 7 | Slide final: URL do repositório + "Obrigada" | Keynote/Google Slides |

Detalhes e tempos de cada imagem: `docs/video-storyboard.md`.

Para a captura 17 (não precisa do Ollama):
```
cd getnet-ai-multiagent-system && source .venv/bin/activate && python -m pytest -q
```

**Não** fabrique capturas. Se não tiver uma resposta JSON real do Mac, use a evidência
documentada em `docs/live-validation.md` e rotule-a como "Validação do desenvolvimento".

## 3. Montar o vídeo

### Opção A: iMovie (já vem no Mac, mais simples)
1. iMovie → **Criar novo → Filme**.
2. Importe as 24 imagens e os 7 áudios.
3. Arraste as imagens para a linha do tempo, na ordem.
4. Selecione todas as imagens → botão **Cortar** (ícone de recorte) → **Ajustar** (*Fit*), para
   desligar o efeito Ken Burns (zoom automático), que atrapalha a leitura de código.
5. Arraste cada áudio para baixo das imagens da sua seção.
6. Ajuste a duração de cada imagem (clique duas vezes no clipe → **Duração**) até as imagens
   da seção somarem a duração do áudio da seção. Use os tempos do storyboard como proporção.
7. Opcional: transição "Dissolver cruzado" de 0,5 s entre as seções (não entre todas as imagens).
8. Opcional: título de texto nos clipes 19/20 ("Validação do desenvolvimento") e 12/17/21
   ("Reproduzido no meu MacBook").

### Opção B: Keynote
Crie um slide por imagem, use **Reproduzir → Gravar slideshow**, ou adicione o áudio por slide e
exporte com **Arquivo → Exportar para → Filme**, com tempos de avanço automático.

## 4. Exportar

- **iMovie:** Arquivo → **Compartilhar → Arquivo…** → Resolução **1080p**, Qualidade **Alta**,
  Compressão **Melhor qualidade** → salvar `.mp4`.
- Configuração recomendada: MP4 (H.264), 1920×1080, 30 fps, áudio AAC 48 kHz, estéreo.

## 5. Verificar antes de enviar

- [ ] Duração total entre 7 e 8 min (limite do PDF: **15 min**).
- [ ] O áudio está sincronizado com as visuais em todas as 7 seções.
- [ ] O texto do código e das tabelas está legível em tela cheia.
- [ ] As evidências do Mac estão rotuladas como "Reproduzido no meu MacBook", e as do desenvolvimento como "Validação do desenvolvimento".
- [ ] Nenhuma tela mostra token, chave de API, `.env`, e-mail pessoal ou abas privadas.
- [ ] Os dados de cliente aparecem identificados como simulados.
- [ ] Assista ao vídeo inteiro uma vez antes do upload.

## 6. Publicar no YouTube como **Não listado**

1. Acesse https://studio.youtube.com (com a conta desejada).
2. **Criar → Enviar vídeos** → selecione o `.mp4`.
3. **Título:** `Getnet AI Hardcore Engineer — Sistema Multiagente (Mariana Nascimento)`.
4. **Descrição:** link do repositório `https://github.com/mrianao/getnet-ai-multiagent-system`.
5. **Público:** "Não, não é conteúdo para crianças".
6. Avance até **Visibilidade** → escolha **Não listado** (*Unlisted*) → **Salvar**.
7. Aguarde o fim do processamento em HD e copie o link (`https://youtu.be/...`).
8. **Teste:** abra o link em uma janela anônima e confirme que o vídeo toca sem login.

## 7. Envio final

- [ ] **Acesso ao repositório:** o repositório é **privado**. Antes de enviar, decida:
      adicionar os avaliadores como colaboradores (GitHub → Settings → Collaborators) ou torná-lo
      público (Settings → General → Change visibility). Sem isso, o avaliador não abre o link.
- [ ] Abra o link do repositório em uma janela anônima (se público) e confira se o README aparece.
- [ ] Abra `docs/submission-email.md`, substitua `<GITHUB_REPOSITORY_URL>` e `<YOUTUBE_VIDEO_URL>`.
- [ ] Envie para **stefany.morais@getnet.net**, a partir do seu e-mail.
- [ ] Guarde o e-mail enviado e o link do vídeo.
