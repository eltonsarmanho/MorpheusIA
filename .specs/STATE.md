# STATE

## Decisions

### AD-001
- **Decision**: O backend segue em Python com FastAPI e pytest. Para o piloto TJPA o armazenamento é SQLite (FTS5 para BM25 e vetores em BLOB com busca exata em NumPy), atrás de portas em `backend/app/domain/ports.py`.
- **Reason**: O acervo tem cerca de 20 mil trechos; busca exata responde em dezenas de milissegundos, não exige serviço extra e roda na VM de 3,9 GB que já hospeda Chatwoot e dashboard.
- **Trade-off**: Uma única instância escreve no SQLite; trocar por Postgres/pgvector exige uma nova implementação de `KnowledgeStore`.
- **Scope**: `backend/`
- **Date**: 2026-10-09
- **Status**: active (substitui a AD-001 anterior, do chatbot comercial removido)

### AD-002
- **Decision**: Orquestração determinística na camada de aplicação; o Agno `Agent` só gera o texto, sem ferramentas de recuperação.
- **Reason**: Domínio, filtros de acesso, abstenção e verificação de fundamentação ficam em código testável com LLM falso, e um trecho não aprovado nunca chega ao modelo.
- **Trade-off**: Menos autonomia do agente; a decisão de buscar mais é limitada a uma reformulação.
- **Scope**: `backend/app/application/answering`
- **Date**: 2026-10-09
- **Status**: active

### AD-003
- **Decision**: Todo conteúdo novo (PDFs com sigilo ou dado pessoal, páginas coletadas) entra como `pending_review` e só vira consultável por decisão registrada de um revisor. O acervo processual só é aprovado automaticamente se o usuário declarar a autorização (`CORPUS_AUTHORIZED=true` com a base registrada) e a triagem automática passar.
- **Reason**: O prompt do projeto proíbe publicar conteúdo sem validação e presumir que um documento é público.
- **Trade-off**: O curador precisa aprovar as coletas institucionais e jurídicas antes de a demonstração responder sobre elas.
- **Scope**: ingestão, coleta e curadoria
- **Date**: 2026-10-09
- **Status**: active

### AD-004
- **Decision**: Backend implantado como contêiner `tjpa_backend` na VM `srv1633081`, publicado só em `127.0.0.1:8300`, atrás do nginx em `/atendimento/`, na rede do Chatwoot para falar com ele internamente. Chatwoot e dashboard não são alterados, exceto `underscores_in_headers on` no vhost do Chatwoot.
- **Reason**: Autorização do usuário em 2026-10-09 para implantar sem remover Chatwoot nem dashboard.
- **Trade-off**: 700 MB do limite de memória do contêiner competem com o Chatwoot na mesma VM.
- **Scope**: implantação
- **Date**: 2026-10-09
- **Status**: active

### AD-005
- **Decision**: O Agent Bot só é ligado à inbox real do WhatsApp com pedido explícito do responsável.
- **Reason**: Ligar faz mensagens reais passarem pelo assistente e chamarem o LLM externo.
- **Trade-off**: O teste com WhatsApp real depende de um passo manual.
- **Scope**: Chatwoot
- **Date**: 2026-10-09
- **Status**: active

### AD-006
- **Decision**: O roteiro TJPA (perfil, identificação, diligência, escolha WhatsApp/Balcão Virtual) estende o fluxo guiado existente (`flow.py` + `handler.py`) e a máquina de estados, sem segundo orquestrador. Pedidos do roteiro vão para as equipes `secretaria` (padrão) e `gabinete` (diligência que cita juiz, gabinete ou urgência). "WhatsApp" é a transferência integrada nesta conversa; o modo link exige número oficial cadastrado. Unidades e contatos vêm de `backend/config/units.yaml`, vazio até haver dado oficial; o link do Balcão é a página geral, declarada como geral.
- **Reason**: Roteiro do TJPA e instrução do responsável (2026-10-27); não há API processual nem cadastro de unidades.
- **Trade-off**: Busca por CPF fica indisponível; a regra secretaria/gabinete é por palavras e pode exigir reatribuição manual no Chatwoot. Nova transição `handoff_requested -> automation_resumed` só para troca de canal após falha.
- **Scope**: `backend/app/application/chat`, `backend/app/domain`, `backend/config/units.yaml`
- **Date**: 2026-10-27
- **Status**: active

### AD-007
- **Decision**: A Ouvidoria da Mulher é um módulo do fluxo guiado existente (`mulher.py`, `triage_mulher.py`, métodos `_mulher_*` do handler), atrás de `OUVIDORIA_MULHER_ENABLED=false`, encaminhando só para o time `ouvidoria_mulher` do Chatwoot. Não há caixa de entrada nova. Contato não seguro: orientar e registrar o pedido sem retorno por este WhatsApp. O aviso de encaminhamento orienta guardar o protocolo TKT para eventual atendimento presencial.
- **Reason**: Aprovação do plano e decisões D1, D3, D5, D6, D8 e D9 do responsável (2026-11-04); time criado por ele.
- **Trade-off**: O time não restringe quem vê a conversa; textos de orientação sem busca livre; dias de atendimento da Ouvidoria não verificados.
- **Scope**: `backend/app/application/chat`, `backend/app/domain/triage_mulher.py`, `backend/config/mulher.yaml`
- **Date**: 2026-11-04
- **Status**: active

### AD-008
- **Decision**: Retomada por protocolo liga um protocolo novo ao anterior (nunca reabre) e só conferindo o contato do Chatwoot; fatos fechados do atendimento encerrado ficam 90 dias; caso da Ouvidoria é restrito (retoma sem citar o assunto; contato não seguro não retoma por chat); a nota de transferência traz o histórico do mesmo contato; o resumo por IA (fase 2) é rascunho rotulado, em segundo plano, e nunca roda para caso restrito. Chaves `TICKET_RESUME_ENABLED` e `TICKET_SUMMARY_AI_ENABLED`.
- **Reason**: Pedido do responsável (2026-11-05) para reduzir a pulverização da informação entre atendentes; fase 2 aprovada para o piloto.
- **Trade-off**: Conferir o contato autentica o telefone, não a pessoa; retenção de 90 dias de dados pessoais vinculados ao contato (D7 segue com a DPO); resumo por IA envia conversas a fornecedor externo (Res. CNJ 615 a enquadrar).
- **Scope**: `backend/app/application/chat`, `backend/app/infrastructure/sqlite`
- **Date**: 2026-11-05
- **Status**: active

## Handoff

- **Feature**: retomada-ticket (`.specs/features/retomada-ticket/`, RTK-01..RTK-18); ouvidoria-mulher e tjpa-roteiro-atendimento também implantadas na VM (código de 2026-11-05) com `TICKET_RESUME_ENABLED`, `TICKET_SUMMARY_AI_ENABLED` e `OUVIDORIA_MULHER_ENABLED` ligadas a pedido do responsável.
- **Phase / Task**: retomada-ticket T1 a T6 implementadas (537 testes passando). Verifier independente: FAIL nas 3 rodadas (sem defeito aberto; lacunas de teste fechadas pelo autor, sem nova verificação independente). RTK-18 (menu com contexto do processo) foi pedido depois da verificação e não foi verificado de forma independente. ouvidoria-mulher: PASS. Sem commits.
- **Next step**: ensaio com a equipe; nova verificação independente da retomada ou decisão do responsável; decidir os 3 pontos de produto do fim de `validation.md`; confirmar nome e horário da Ouvidoria; `chatwoot_setup --apply` (rótulos e atributos) só com aprovação.
- **Blockers**: Agent Bot ligado à caixa real (decisão do responsável); retenção do relato e dos fatos (DPO); enquadramento do resumo por IA na Res. CNJ 615 (área de IA do TJPA).
- **Branch**: main
- **Feature anterior**: tjpa-atendimento-rag (avaliação e implantação em andamento; ver `docs/AVALIACAO.md`)
