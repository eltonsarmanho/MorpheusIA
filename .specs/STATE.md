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

## Handoff

- **Feature**: tjpa-roteiro-atendimento / `.specs/features/tjpa-roteiro-atendimento/`
- **Phase / Task**: Execute T1 a T9 implementados e testados localmente (sem commits; o responsável não pediu). Verifier independente: ver `validation.md`.
- **Next step**: homologação com número de teste (ver `docs/OPERACAO.md` §6.3); preencher `backend/config/units.yaml` com dados oficiais; confirmar a regra secretaria/gabinete.
- **Blockers**: dados oficiais de unidades, TeleJudiciário e links diretos do Balcão; API processual para CPF.
- **Branch**: main
- **Feature anterior**: tjpa-atendimento-rag (avaliação e implantação em andamento; ver `docs/AVALIACAO.md`)
