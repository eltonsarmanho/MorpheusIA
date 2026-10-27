# Roteiro de atendimento TJPA (perfil, identificação, diligência e canal humano) Specification

Fonte do fluxo: `Roteiro_e_fluxo_TJPA_WhatsApp_Balcao_Virtual.pdf` (seções 1 a 8 e gráficos 1 e 2).
Feature incremental sobre `tjpa-atendimento-rag`: reutiliza a máquina de estados de `backend/app/domain/handoff.py`, o
protocolo TKT, a idempotência por evento e o fluxo guiado de `backend/app/application/chat/{flow,handler}.py`. Não cria
um segundo orquestrador.

## Problem Statement

O fluxo guiado atual (menu principal com Consultar processo, Balcão Virtual, Termos jurídicos, Atendente, Encerrar)
não identifica o perfil de quem fala, não oferece a escolha entre WhatsApp e Balcão Virtual, não recebe a diligência
do advogado em campo livre e não tem um cadastro de unidades. O roteiro do TJPA exige esses passos, com regras fortes
de honestidade: não confirmar envio sem confirmação técnica, não tratar a abertura de um link como envio, não
fabricar contatos e não presumir que um processo foi localizado.

## Reconhecimento: existente vs. lacunas

| Roteiro | Já existe (verificado no código e nos testes) | Lacuna |
| --- | --- | --- |
| 1. Abertura e perfil | Protocolo TKT, saudação e menu principal; `Profile` (cidadao/advogado/indefinido) inferido por palavras | Menu de 3 perfis; perfil escolhido persistente e não sobrescrito pela heurística; identidade Júlia |
| 2. Informações gerais | Menu "Balcão Virtual" com 3 perguntas ao RAG institucional | Menu de 5 itens; cidade/unidade; cadastro de unidades; TeleJudiciário só se configurado |
| 3.1 Número | Busca por número no acervo de demonstração, "não encontrei" | Validação dos dígitos verificadores (CNJ, módulo 97); distinguir formato inválido de ausente |
| 3.2 CPF | Nenhum | Validação do CPF; porta de busca; indisponibilidade honesta (o índice mascara CPF) |
| 3.3 Nome / Vara | Nenhum | Busca por nome nas capas aprovadas; seleção explícita; refinamento; pergunta sobre a Vara |
| 4. Diligência do advogado | Nenhum | Campo livre preservado integralmente, associado só a processo/unidade confirmados |
| 5. Menu do processo | Cronologia, Fazer pergunta, Outro processo, Atendente, Encerrar | Audiências, Situação atual, Providência/diligência, rótulos por perfil |
| 6. Canal humano | Transferência confirmada por equipe+status, tentativas, bloqueio do bot | Escolha explícita WhatsApp/Balcão; nota de contexto; modo link; Balcão com texto copiável; recuperação de falha |
| 7. Continuidade | Botões Menu/Atendente/Encerrar | Menu "Como deseja continuar?" |
| 8. Regras | Auditoria, idempotência | Cadastro por unidade com fonte e data de verificação |

## Divergências entre fontes (registradas antes de resolver)

| ID | Divergência | Resolução |
| --- | --- | --- |
| DIV-01 | O roteiro exige link do Balcão Virtual **específico da unidade**; o único link fornecido (pelo responsável, 2026-10-27) é a página geral `…/844287-balcao-virtual.xhtml`, onde se escolhe a unidade | O cadastro aceita link por unidade; enquanto não houver, a página geral é exibida **declarada como geral** ("não tenho o link direto da unidade"). Nunca é apresentada como link da unidade |
| DIV-02 | O roteiro fala em "WhatsApp da unidade"; no piloto, o WhatsApp é a própria conversa no Chatwoot | Modo `chatwoot` (padrão): "WhatsApp" = transferência integrada nesta conversa para a equipe, com nota de contexto, confirmada pela API. Modo `link` só com número oficial cadastrado |
| DIV-03 | O roteiro pressupõe consulta processual (CPF, audiências, situação); o piloto só tem o acervo de demonstração do **TJAP** | CPF: indisponível (porta sem implementação). Nome, audiências e situação: somente no acervo de demonstração, sempre com o aviso de que não é o TJPA em tempo real |
| DIV-04 | Equipes: a spec anterior usa 5 equipes por domínio; o responsável criou `secretaria` e `gabinete` para o piloto (2026-10-27) | O caminho do roteiro (escolha de canal) usa `secretaria` por padrão e `gabinete` quando a diligência menciona juiz/gabinete/urgência; os demais encaminhamentos do RAG seguem inalterados |
| DIV-05 | O roteiro abre com o menu de 3 perfis; o comportamento atual responde a pergunta livre direto | Saudação/menu mostram o menu de perfis; pergunta livre continua sendo respondida (princípio "não forçar menu"), sem regressão |

## Assumptions & Open Questions

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --- | --- | --- | --- |
| Equipes do roteiro | `secretaria` (padrão) e `gabinete` (diligência que cita juiz, gabinete, magistrado, desembargador ou urgência) | Instrução do responsável em 2026-10-27; a regra é determinística e auditável; a equipe pode reatribuir no Chatwoot | y (equipes) / n (regra de palavras) |
| Link do Balcão Virtual | Página geral fornecida, declarada como geral (DIV-01) | Único link real disponível | y (link) |
| Modo do WhatsApp | `chatwoot` (transferência nesta conversa) | No piloto, o WhatsApp é a conversa do Chatwoot (DIV-02) | n (agent default) |
| Busca por nome | Capas aprovadas do acervo de demonstração, nome completo, todas as palavras na mesma parte | Dados já autorizados como públicos (AD-003); sem similaridade | n (agent default) |
| Busca por CPF | Indisponível | O índice mascara CPF e não há API processual | y (restrição do piloto) |
| Pergunta livre sem perfil | Continua respondida, sem forçar o menu (DIV-05) | Evita regressão e segue o princípio "não forçar menu" | n (agent default) |
| Limite da diligência | 3.000 caracteres; acima disso pede versão menor, sem truncar | Cabe na nota do Chatwoot e em uma mensagem do WhatsApp | n (agent default) |

**Open questions:** none - all resolved or logged above.

## User Stories

### P1: Abertura e perfil

**User Story**: Como pessoa atendida, quero dizer qual é o meu caso para receber linguagem e opções adequadas.

**Acceptance Criteria**:

1. WHEN a conversa recebe saudação ou pedido de menu e não há perfil escolhido, the system SHALL apresentar a Júlia (assistente virtual, não pessoa) e o menu com os 3 perfis do roteiro, mais "Falar com atendente" e "Encerrar".  <!-- RTA-01 -->
2. WHEN o usuário escolhe um perfil, the system SHALL persistir `parte_testemunha`, `advogado` ou `geral` e marcar o perfil como escolhido, de modo que a heurística de palavras não o substitua.  <!-- RTA-02 -->
3. The system SHALL usar o perfil só para linguagem e rótulos; filtros de acesso, evidência e critérios de encaminhamento não mudam com o perfil.  <!-- RTA-03 -->

### P1: Informações gerais

**User Story**: Como pessoa sem processo, quero endereço, contatos e horário da unidade, sem dados inventados.

**Acceptance Criteria**:

1. WHEN o perfil é `geral`, the system SHALL mostrar o menu Endereço, Telefones e canais, Horário, Balcão Virtual, Outra informação (mais escapes).  <!-- RTA-04 -->
2. WHEN um item de endereço, telefone ou horário é escolhido e há unidades cadastradas, the system SHALL pedir cidade/unidade e responder somente com o campo cadastrado e sua fonte; campo ausente é declarado ausente.  <!-- RTA-05 -->
3. IF não há unidade cadastrada ou não foi possível identificá-la, THEN the system SHALL consultar a base institucional aprovada (RAG) e oferecer o TeleJudiciário somente se o contato estiver configurado.  <!-- RTA-06 -->

### P1: Identificação do processo

**User Story**: Como parte ou advogado, quero localizar o processo por número, CPF ou nome, sem que o sistema presuma o processo.

**Acceptance Criteria**:

1. WHEN o perfil é parte/testemunha ou advogado, the system SHALL oferecer: Sei o número, Sei o CPF, Não sei nenhum.  <!-- RTA-07 -->
2. WHEN o número tem formato CNJ, the system SHALL localizá-lo somente no acervo aprovado; IF não consta e os dígitos verificadores falham, THEN pedir correção; IF não consta e é válido, THEN declarar que não foi localizado (sem consulta ao PJe). Texto com dígitos fora do formato pede correção.  <!-- RTA-08 -->
3. WHEN o usuário envia um CPF, the system SHALL validar os dígitos sem repetir o número; IF inválido, THEN pedir correção sem buscar; IF válido, THEN usar a porta de busca e, sem fonte autorizada, declarar a indisponibilidade. O CPF não vai ao LLM, ao log nem à auditoria.  <!-- RTA-09 -->
4. WHEN o usuário informa nome (2+ palavras), the system SHALL buscar nas capas aprovadas e mostrar só número, classe e participação; 2 a 5 candidatos exigem seleção explícita ("Opção N" ou o número); mais de 5 pedem cidade/unidade para filtrar; nenhum ou ainda ambíguo leva à pergunta sobre a Vara. Nunca seleciona por similaridade.  <!-- RTA-10 -->
5. WHEN o usuário diz saber a Vara, the system SHALL pedir cidade e unidade, registrá-las como unidade **informada** (processo não localizado) e seguir à escolha de canal; WHEN não sabe, oferecer o TeleJudiciário configurado ou o atendimento humano.  <!-- RTA-11 -->

### P1: Processo localizado e menus

**User Story**: Como parte ou advogado, quero ver audiências, situação e fazer perguntas sobre o processo localizado.

**Acceptance Criteria**:

1. WHEN um processo é localizado, the system SHALL guardar número e unidade do processo; advogado recebe "Pedir diligência / Ver o processo"; demais perfis, o menu do processo.  <!-- RTA-12 -->
2. The menu do processo SHALL ter Audiências, Situação atual (rótulo "Como está o processo" para parte), Pedir providência/diligência, Fazer pergunta, Cronologia, Falar com atendente, Outro processo, Encerrar, Menu (≤ 10 linhas).  <!-- RTA-13 -->
3. Audiências e Situação atual SHALL ser perguntas ao RAG com verificação de fundamentação já existente, com o aviso do acervo de demonstração; sem evidência, abstenção honesta.  <!-- RTA-14 -->

### P1: Diligência e canal humano

**User Story**: Como advogado, quero descrever a diligência e escolher WhatsApp ou Balcão Virtual, sabendo exatamente o que foi enviado.

**Acceptance Criteria**:

1. WHEN o advogado escolhe diligência, the system SHALL pedir a descrição em campo livre; IF vazio, THEN pedir de novo; IF acima do limite, THEN pedir para resumir sem truncar; ELSE guardar o texto exatamente como recebido.  <!-- RTA-15 -->
2. WHEN há diligência ou pedido de atendimento com unidade identificada, the system SHALL perguntar "Como deseja ser atendido pela [unidade]?" mostrando só canais disponíveis; sem unidade, o pedido de atendente segue a transferência direta atual.  <!-- RTA-16 -->
3. WHEN o canal WhatsApp é escolhido no modo `chatwoot`, the system SHALL registrar uma nota privada de contexto (uma única vez) e só então transferir; SHALL informar sucesso só após a confirmação de equipe+status, dizendo que o encaminhamento não significa diligência realizada.  <!-- RTA-17 -->
4. WHEN o canal WhatsApp é escolhido no modo `link`, the system SHALL usar só número oficial cadastrado e válido, gerar o link com a mensagem preenchida e avisar que abrir o link não envia nada; sem número, declarar e oferecer outro canal.  <!-- RTA-18 -->
5. WHEN o Balcão Virtual é escolhido, the system SHALL mostrar o link validado (da unidade ou, declarado, o geral), o horário só se cadastrado e, havendo diligência, o texto integral em mensagem própria para copiar; SHALL NOT afirmar que o atendente recebeu nada.  <!-- RTA-19 -->
6. IF a transferência não é confirmada, THEN the system SHALL preservar a diligência e oferecer: Tentar novamente, Outro canal e Telefone da unidade (só se cadastrado); trocar de canal cancela a tentativa pendente com auditoria e nunca envia aos dois canais.  <!-- RTA-20 -->
7. The nota de contexto SHALL conter só campos disponíveis (canal, perfil, processo, unidade, solicitante, data/hora, diligência ou motivo, número de atendimento) e nunca campos inventados.  <!-- RTA-21 -->

### P2: Estado, privacidade e continuidade

**User Story**: Como equipe do piloto, quero que estado, idempotência e privacidade continuem garantidos no novo fluxo.

**Acceptance Criteria**:

1. The system SHALL manter idempotência por evento, bloqueio do bot em atendimento humano e as regras de encerramento atuais; o encerramento apaga diligência e escolhas pendentes.  <!-- RTA-22 -->
2. The system SHALL NOT registrar em log ou auditoria o texto da diligência, CPF ou nome buscado.  <!-- RTA-23 -->
3. The system SHALL oferecer o menu de continuidade (outra informação deste processo, outro processo, informações gerais, encerrar) após o Balcão Virtual e as respostas de informações gerais.  <!-- RTA-24 -->
4. The protocolo TKT SHALL ser descrito como número de atendimento interno, não protocolo judicial.  <!-- RTA-25 -->
5. The títulos de opções SHALL caber nos limites do WhatsApp (botão 20, linha de lista 24, até 10 itens).  <!-- RTA-26 -->

### P2: Identidade Júlia / EscutAI

**User Story**: Como TJPA, quero que todas as mensagens sigam a voz da Júlia sem prometer o que o sistema não faz.

**Acceptance Criteria**:

1. The system SHALL centralizar a identidade (nome, assinatura "A Justiça que escuta você.", princípios de voz) em um único módulo usado pelo prompt do LLM e pelos textos determinísticos.  <!-- RTA-27 -->
2. Mensagens determinísticas (abertura, erros, abstenções, transferência, encerramento) SHALL evitar jargão técnico ("RAG", "handoff", "ticket") e alegações de consulta, envio ou execução não realizadas.  <!-- RTA-28 -->

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| RTA-01 | P1: Abertura e perfil | Execute | Implemented |
| RTA-02 | P1: Abertura e perfil | Execute | Implemented |
| RTA-03 | P1: Abertura e perfil | Execute | Implemented |
| RTA-04 | P1: Informações gerais | Execute | Implemented |
| RTA-05 | P1: Informações gerais | Execute | Implemented |
| RTA-06 | P1: Informações gerais | Execute | Implemented |
| RTA-07 | P1: Identificação do processo | Execute | Implemented |
| RTA-08 | P1: Identificação do processo | Execute | Implemented |
| RTA-09 | P1: Identificação do processo | Execute | Implemented |
| RTA-10 | P1: Identificação do processo | Execute | Implemented |
| RTA-11 | P1: Identificação do processo | Execute | Implemented |
| RTA-12 | P1: Processo localizado e menus | Execute | Implemented |
| RTA-13 | P1: Processo localizado e menus | Execute | Implemented |
| RTA-14 | P1: Processo localizado e menus | Execute | Implemented |
| RTA-15 | P1: Diligência e canal humano | Execute | Implemented |
| RTA-16 | P1: Diligência e canal humano | Execute | Implemented |
| RTA-17 | P1: Diligência e canal humano | Execute | Implemented |
| RTA-18 | P1: Diligência e canal humano | Execute | Implemented |
| RTA-19 | P1: Diligência e canal humano | Execute | Implemented |
| RTA-20 | P1: Diligência e canal humano | Execute | Implemented |
| RTA-21 | P1: Diligência e canal humano | Execute | Implemented |
| RTA-22 | P2: Estado, privacidade e continuidade | Execute | Implemented |
| RTA-23 | P2: Estado, privacidade e continuidade | Execute | Implemented |
| RTA-24 | P2: Estado, privacidade e continuidade | Execute | Implemented |
| RTA-25 | P2: Estado, privacidade e continuidade | Execute | Implemented |
| RTA-26 | P2: Estado, privacidade e continuidade | Execute | Implemented |
| RTA-27 | P2: Identidade Júlia / EscutAI | Execute | Implemented |
| RTA-28 | P2: Identidade Júlia / EscutAI | Execute | Implemented |

## Out of Scope

| Item | Motivo |
| --- | --- |
| Busca por CPF real, audiências e situação em tempo real | Não há API processual autorizada (porta sem implementação) |
| Cadastro real de unidades, telefones, WhatsApp oficial, horários e TeleJudiciário | Dados não fornecidos; o cadastro começa vazio e é preenchido pela equipe |
| Integração com a fila do Balcão Virtual | Inexistente no piloto |
| Coleta de OAB/UF e contato de retorno | A conversa já é o canal de retorno; não pedir dado desnecessário |

## Success Criteria

- Todos os critérios RTA-01..RTA-28 cobertos por testes offline; suíte anterior sem regressão.
