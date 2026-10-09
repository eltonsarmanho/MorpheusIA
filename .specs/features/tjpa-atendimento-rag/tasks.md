# Atendimento omnichannel com Hybrid RAG (piloto TJPA) Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user - do not proceed without it.**

---

**Design**: decisões em `docs/ARQUITETURA.md` (a fase Design foi feita inline; não há `design.md` separado)
**Status**: In Progress. Este arquivo foi escrito durante a execução, não antes: as tarefas foram feitas em pequenos incrementos validados e depois registradas aqui com seus testes reais.

---

## Test Coverage Matrix

> Guidelines found: `backend/pytest.ini` (marcadores `live` e `corpus`), `docs/OPERACAO.md`. Nenhum `AGENTS.md`, `CONTRIBUTING.md` ou limite de cobertura; defaults fortes aplicados.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| Domínio (políticas, privacidade, estados) | unit | todos os ramos; 1:1 com os ACs; casos de borda listados | `backend/tests/unit/*.py` | `.venv/bin/python -m pytest tests/unit` |
| Aplicação (ingestão, RAG, orquestração, coleta, Chatwoot) | integration | caminho feliz, bordas e falhas de cada AC | `backend/tests/integration/*.py` | `.venv/bin/python -m pytest tests/integration` |
| Infraestrutura (SQLite, HTTP) | integration | consultas-chave, filtros de acesso, concorrência | `backend/tests/integration/*.py` | idem |
| API | integration | rotas com e sem token, validação de entrada | `backend/tests/integration/test_api.py` | idem |
| Índice real | corpus | portões de promoção | `backend/tests/eval/test_gates.py` | `.venv/bin/python -m pytest -m corpus tests/eval` |
| Frontend | none | verificação manual no navegador | `frontend/*` | `node --check frontend/app.js` |

## Gate Check Commands

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | tarefas só com testes unitários | `cd backend && .venv/bin/python -m pytest tests/unit -q` |
| Full | tarefas com integração | `cd backend && .venv/bin/python -m pytest -q` |
| Build | fim de fase | `cd backend && .venv/bin/python -m pytest -q && .venv/bin/python -m pytest -m corpus tests/eval -q && .venv/bin/python -m evals.run_eval --mode retrieval` |

---

## Execution Plan

Cada fase só começa quando a anterior termina; por isso a primeira tarefa de cada fase declara `Depends on: None` dentro da fase.

### Phase 1: Fundação

```
T1 → T2 → T3
```

### Phase 2: Ingestão e curadoria

```
T4 → T5 → T6 → T7
```

### Phase 3: Recuperação e resposta

```
T8 → T9 → T10
```

### Phase 4: Coleta

```
T11 → T12
```

### Phase 5: Integração

```
T13 → T14 → T15 → T16
```

### Phase 6: Qualidade e entrega

```
T17 → T18
```

---

## Task Breakdown

### T1: Modelos, políticas e máscara de dados pessoais

**What**: entidades do domínio, triagem de acesso, números processuais e máscara de dados pessoais.
**Where**: `backend/app/domain/policies.py`
**Depends on**: None
**Reuses**: nada (primeiro código do novo projeto)
**Requirement**: CUR-01, CUR-02, SEC-01

**Done when**:

- [x] Triagem mantém `pending_review` em sigilo, tipo sensível, nome sensível e acervo sem autorização
- [x] Máscara cobre CPF, RG, CNH, telefone, e-mail, CEP e números de 11 dígitos, sem tocar em número de processo, valor ou id

**Tests**: unit
**Gate**: quick

---

### T2: Máquina de estados do atendimento

**What**: estados e transições válidas do atendimento.
**Where**: `backend/app/domain/handoff.py`
**Depends on**: T1
**Reuses**: `backend/app/domain/models.py`
**Requirement**: CHW-01, CHW-02

**Done when**:

- [x] Transições inválidas levantam `InvalidTransition`
- [x] Só `bot_active` e `automation_resumed` permitem resposta do bot

**Tests**: unit
**Gate**: quick

---

### T3: Armazenamento SQLite do conhecimento e do estado operacional

**What**: documentos, trechos, FTS5, vetores, decisões de curadoria, estado de conversa e eventos processados.
**Where**: `backend/app/infrastructure/sqlite/knowledge_store.py`
**Depends on**: T2
**Reuses**: `backend/app/domain/ports.py`
**Requirement**: RAG-02, RAG-03, CHW-05

**Done when**:

- [x] Filtro `approved` + `public` + ativo aplicado em BM25, vetor e carga de evidências
- [x] Acesso concorrente seguro (corrida de cursor corrigida)
- [x] Decisão humana preservada por hash de conteúdo

**Tests**: integration
**Gate**: full

---

### T4: Leitura estrutural do PDF do PJe

**What**: rodapé, capa, tabela de documentos e segmentação por documento.
**Where**: `backend/app/application/ingestion/pje_parser.py`
**Depends on**: None
**Reuses**: nada
**Requirement**: ING-02, ING-07

**Done when**:

- [x] Rodapé mais externo vence em PDFs aninhados
- [x] Campos ausentes viram `desconhecido`
- [x] Parser aplicado aos 10 PDFs reais sem erro

**Tests**: unit
**Gate**: quick

---

### T5: Normalização e divisão em trechos

**What**: normalização sem alterar números, reparo de texto com codificação quebrada e chunking.
**Where**: `backend/app/application/ingestion/text_processing.py`
**Depends on**: T4
**Reuses**: nada
**Requirement**: ING-05, ING-06

**Done when**:

- [x] Hifenização unida, números e datas preservados
- [x] Trechos de até 520 caracteres, dentro do limite de 900 do AC

**Tests**: unit
**Gate**: quick

---

### T6: Serviço de ingestão

**What**: inventário, OCR seletivo, qualidade por página, deduplicação, reprocessamento e relatório.
**Where**: `backend/app/application/ingestion/service.py`
**Depends on**: T5
**Reuses**: `backend/app/infrastructure/pdf/poppler.py`
**Requirement**: ING-01, ING-03, ING-04, ING-08, ING-09, ING-10

**Done when**:

- [x] 10 PDFs ingeridos, 0 erros, PDFs inalterados
- [x] Segunda execução pula arquivos com mesmo SHA-256
- [x] Relatório JSON gerado

**Tests**: integration
**Gate**: full

---

### T7: Serviço de curadoria

**What**: aprovar, rejeitar e reindexar com revisor e motivo.
**Where**: `backend/app/application/curation/service.py`
**Depends on**: T6
**Reuses**: `backend/app/infrastructure/sqlite/knowledge_store.py`
**Requirement**: CUR-03

**Done when**:

- [x] Aprovar indexa; rejeitar ou `stale` retira da busca
- [x] Revisor e motivo obrigatórios

**Tests**: integration
**Gate**: full

---

### T8: Recuperação híbrida

**What**: BM25 + vetorial + RRF, filtros, reranker opcional e critérios de abstenção.
**Where**: `backend/app/application/retrieval/hybrid.py`
**Depends on**: None
**Reuses**: `backend/app/infrastructure/sqlite/knowledge_store.py`
**Requirement**: RAG-01, RAG-02, RAG-03, RAG-04, RAG-09

**Done when**:

- [x] Domínios isolados e documentos não aprovados nunca recuperados
- [x] Processo ausente do acervo gera abstenção
- [x] Falha do reranker não derruba a busca

**Tests**: integration
**Gate**: full

---

### T9: Intenção, prompt e verificação de fundamentação

**What**: classificação de intenção e perfil, system prompt e verificador de fundamentação.
**Where**: `backend/app/application/answering/grounding.py`
**Depends on**: T8
**Reuses**: `backend/app/application/answering/prompts.py`
**Requirement**: ORQ-01, ORQ-02, RAG-06, RAG-07, SEC-02

**Done when**:

- [x] Data, valor, processo e número sem lastro reprovam a resposta
- [x] Instruções em documentos são removidas do contexto

**Tests**: unit
**Gate**: quick

---

### T10: Orquestrador

**What**: fluxo completo de resposta, esclarecimento, abstenção e encaminhamento.
**Where**: `backend/app/application/answering/orchestrator.py`
**Depends on**: T9
**Reuses**: `backend/app/application/retrieval/hybrid.py`
**Requirement**: ORQ-03, ORQ-04, RAG-05, RAG-08

**Done when**:

- [x] Resposta cita fonte e traz aviso de que não é consulta em tempo real
- [x] Duas abstenções seguidas encaminham
- [x] Recência usa a data do documento

**Tests**: integration
**Gate**: full

---

### T11: Fontes autorizadas, busca HTTP e extração

**What**: lista de fontes, busca respeitosa e extração de páginas com vigência.
**Where**: `backend/app/application/collection/sources.py`
**Depends on**: None
**Reuses**: `backend/app/infrastructure/web/fetcher.py`
**Requirement**: COL-01, COL-07

**Done when**:

- [x] URL fora da lista, sem TLS ou de outro domínio é recusada e registrada
- [x] `robots.txt` bloqueante ou inacessível suspende a coleta

**Tests**: integration
**Gate**: full

---

### T12: Serviço de coleta e curadoria de B e C

**What**: coleta, duplicidade, obsolescência e divergência.
**Where**: `backend/app/application/collection/service.py`
**Depends on**: T11
**Reuses**: `backend/app/application/collection/extractors.py`
**Requirement**: COL-02, COL-03, COL-04, COL-05, COL-06, COL-08, COL-09

**Done when**:

- [x] Tudo entra como `pending_review`
- [x] Texto original por artigo preservado, sem resumo gerado

**Tests**: integration
**Gate**: full

---

### T13: Cliente e tratamento de eventos do Chatwoot

**What**: cliente HTTP com nova tentativa e tratamento idempotente de eventos.
**Where**: `backend/app/application/chat/handler.py`
**Depends on**: None
**Reuses**: `backend/app/infrastructure/chatwoot/client.py`
**Requirement**: CHW-02, CHW-03, CHW-04, CHW-05, CHW-06, CHW-07, CHW-09, CHW-10, CHW-11

**Done when**:

- [x] Evento duplicado processado uma vez
- [x] Bot silencioso depois da transferência e só retoma por comando explícito
- [x] Transferência não confirmada não é anunciada

**Tests**: integration
**Gate**: full

---

### T14: API FastAPI

**What**: webhook autenticado, console, curadoria, auditoria e retomada.
**Where**: `backend/app/interfaces/api/main.py`
**Depends on**: T13
**Reuses**: `backend/app/container.py`
**Requirement**: UI-02, CHW-05

**Done when**:

- [x] Rotas administrativas e console exigem token
- [x] Webhook responde 200 e processa em segundo plano

**Tests**: integration
**Gate**: full

---

### T15: Preparação idempotente do Chatwoot

**What**: equipes, etiquetas, Agent Bot e vínculo com a inbox, com dry-run.
**Where**: `backend/app/interfaces/chatwoot_setup.py`
**Depends on**: T14
**Reuses**: `backend/app/infrastructure/chatwoot/client.py`
**Requirement**: CHW-08, CHW-12

**Done when**:

- [x] Etiquetas criadas e segunda execução não cria nada
- [ ] Agent Bot criado e testado com conversa real (depende da implantação, ver T18)

**Tests**: none
**Gate**: build

---

### T16: Console web

**What**: página de conversa e curadoria.
**Where**: `frontend/app.js`
**Depends on**: T15
**Reuses**: `frontend/index.html`
**Requirement**: UI-01

**Done when**:

- [x] Lista, aprova e rejeita documentos
- [ ] Verificação manual no navegador

**Tests**: none
**Gate**: build

---

### T17: Avaliação e portões de promoção

**What**: conjuntos de perguntas, executor de avaliação e portões sobre o índice real.
**Where**: `backend/evals/run_eval.py`
**Depends on**: None
**Reuses**: `backend/tests/eval/test_gates.py`
**Requirement**: RAG-04, SEC-01

**Done when**:

- [x] Recall, MRR e latência medidos
- [ ] Execução ponta a ponta com a MariTalk registrada em `docs/AVALIACAO.md`

**Tests**: integration
**Gate**: build

---

### T18: Implantação na VM

**What**: contêiner do backend na VM, ao lado do Chatwoot e do dashboard.
**Where**: `deploy/docker-compose.yml`
**Depends on**: T17
**Reuses**: `backend/Dockerfile`
**Requirement**: CHW-08

**Done when**:

- [ ] `/health` responde na VM e o fluxo foi testado por uma conversa real no Chatwoot
- [ ] Chatwoot e dashboard continuam saudáveis

**Tests**: none
**Gate**: build
