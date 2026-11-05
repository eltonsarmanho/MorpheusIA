# Retomada por protocolo e resumo para o atendente Specification

Feature incremental sobre `tjpa-roteiro-atendimento` e `ouvidoria-mulher`: reutiliza o protocolo TKT, a nota de contexto, a transferência
confirmada e o encerramento. Plano e evidências em `/home/nees/.claude/plans/majestic-dreaming-kitten.md` e `docs/Retomada/`.

## Problem Statement

Quem volta ao TJPA recomeça do zero: cada retorno vira uma conversa nova no Chatwoot, o protocolo não guarda contexto e a nota ao
atendente só existe em parte dos caminhos. A pessoa repete o caso a cada atendente e a informação fica espalhada.

## Assumptions & Open Questions

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --- | --- | --- | --- |
| Resumo por fases | Fase 1 sem IA; fase 2 com IA, rotulada, atrás de chave | Resposta do responsável (2026-11-05) | y |
| Fase 2 aprovada para o piloto | Resumo por IA existe e pode ser ligado por `TICKET_SUMMARY_AI_ENABLED` | Mensagem do responsável (2026-11-05) | y |
| Retenção dos fatos | 90 dias, apagamento automático | Resposta do responsável; D7 segue com a DPO | y (piloto) |
| Ouvidoria da Mulher | Retoma só com protocolo digitado e mesmo contato, sem citar o assunto; contato não seguro não retoma por chat | Resposta do responsável (2026-11-05) | y |
| Telefone de retorno | Perguntado só antes do Balcão Virtual, pode recusar; "este número" usa o telefone do contato | Chamada do Balcão cai e o atendente retorna por WhatsApp | y |
| Dono do protocolo | `sender.id` do contato do Chatwoot; autentica o telefone, não a pessoa | Não há outro identificador | n (agent default) |
| Protocolos antigos | Sem dono, não retomáveis, sem preenchimento retroativo | Dado inexistente | n (agent default) |
| Leitura de mensagens pela IA | Uma página da API (últimas 20 mensagens públicas) da conversa e da anterior vinculada | Limite simples da API | n (agent default) |
| Anúncio ao usuário do uso de IA (Res. CNJ 615, art. 33) | Só rótulo na nota do atendente; avaliar anúncio com a área de IA do TJPA | O texto não cita chatbot | n (agent default) |

**Open questions:** none blocking; the defaults marked n await the DPO and the area de IA do TJPA.

## User Stories

### P1: Retomar um atendimento

**User Story**: Como pessoa já atendida, quero retomar pelo protocolo para não recomeçar.

**Acceptance Criteria**:

1. WHEN a chave está ligada, the system SHALL incluir "Retomar atendimento" no menu principal antes de "Falar com atendente"; WHILE desligada, the menu SHALL ser o anterior e a opção SHALL ser texto comum.  <!-- RTK-01 -->
2. The system SHALL gravar o id do contato do Chatwoot no protocolo só quando a mensagem vem do próprio contato (`sender.id` com tipo `contact` ou ausente, depois `conversation.meta.sender.id` e `contact_inbox.contact_id`) e a chave está ligada; remetente explícito de outro tipo (bot, agente, usuário) NEVER vira dono; IF nenhum id é encontrado, THEN the system SHALL auditar só o tipo e os nomes dos campos do evento, sem valores.  <!-- RTK-02 -->
3. WHEN a pessoa digita um protocolo do mesmo contato, the system SHALL ligar o novo protocolo ao anterior e restaurar perfil, unidade e processo somente onde estiverem vazios; IF o protocolo não existe, é de outro contato, não tem dono ou é sem chat, THEN the system SHALL dar exatamente a mesma resposta; WHEN ocorrem 3 tentativas sem sucesso, the system SHALL oferecer o atendimento humano.  <!-- RTK-03 -->
4. The system SHALL gravar a ligação uma única vez, nunca para o próprio protocolo; the system SHALL tratar um protocolo digitado dentro de diligência ou relato como texto.  <!-- RTK-04 -->
5. WHEN um protocolo é encerrado e tem dono e visibilidade normal, the system SHALL guardar fatos fechados (perfil, processo informado, unidade informada, motivo, telefone de retorno, resumo do atendente) uma única vez e SHALL NOT guardar diligência nem relato.  <!-- RTK-05 -->
6. WHEN passam 90 dias do encerramento, the system SHALL apagar os fatos e o dono e manter a linha do protocolo.  <!-- RTK-06 -->
7. WHEN a pessoa escolhe "Não sei o número", the system SHALL listar só protocolos do mesmo contato com visibilidade normal, sem o protocolo atual.  <!-- RTK-08 -->

### P1: Casos sensíveis

**User Story**: Como mulher em situação de violência, quero retomar sem expor o assunto.

**Acceptance Criteria**:

1. WHEN a pessoa entra na Ouvidoria da Mulher ou recebe a oferta por texto de violência, the system SHALL marcar o protocolo como restrito; IF o contato é informado como não seguro, THEN the system SHALL marcá-lo sem chat e soltar o dono; WHEN a pessoa retoma um protocolo restrito, the system SHALL NOT citar o assunto, SHALL encaminhar ao time da Ouvidoria com nota que traz só protocolos, data e link da conversa anterior, e SHALL NOT listar nem citar o caso a outros times.  <!-- RTK-07 -->

### P1: Resumo para o atendente

**User Story**: Como atendente, quero receber o histórico para não pedir tudo de novo.

**Acceptance Criteria**:

1. WHEN há transferência e a chave está ligada, the system SHALL gravar a nota de contexto uma vez, também no caminho sem unidade, com os últimos 5 protocolos do mesmo contato (marcados "vinculado pela pessoa" ou "não confirmado"), equipe, motivo, resumo do atendente e link de cada conversa; IF montar o histórico falha, THEN the system SHALL enviar a nota básica e concluir a transferência.  <!-- RTK-09 -->
2. WHEN a pessoa escolhe o Balcão Virtual e a chave está ligada, the system SHALL perguntar uma vez por protocolo se pode retornar por WhatsApp (neste número, outro número validado ou prefiro não informar), gravar nota privada sem transferir e pedir que informe o protocolo ao atendente.  <!-- RTK-10 -->
3. WHEN o atendente envia `/encerrar` com texto, the system SHALL guardar o texto original (máximo 600 caracteres, CPF mascarado) como resumo do atendente.  <!-- RTK-11 -->

### P2: Resumo por IA (fase 2)

**Acceptance Criteria**:

1. WHILE a chave de IA está ligada, WHEN a transferência é confirmada, the system SHALL gerar o resumo em segundo plano, fora do lock da conversa, uma tentativa por protocolo, e postá-lo como nota privada rotulada como rascunho de IA só se a conversa ainda estiver com o atendente; the system SHALL NOT gerar resumo para caso restrito, sem chat ou da Ouvidoria; IF a IA falha, THEN the transferência SHALL permanecer concluída.  <!-- RTK-12 -->
2. The system SHALL enviar ao modelo só mensagens públicas com dados pessoais mascarados, tratar a conversa como dado, exigir saída em campos fechados e descartar todo número ou data que não esteja no texto-fonte.  <!-- RTK-13 -->

### P1: Retomada visível ao atendente

**User Story**: Como atendente, quero ver o atendimento anterior assim que a pessoa o retoma, sem esperar a transferência.

**Acceptance Criteria**:

1. WHEN a pessoa retoma um protocolo normal, the system SHALL gravar uma nota privada com o protocolo anterior, seus fatos, se ainda está aberto e o link da conversa; WHILE a chave de IA está ligada, the system SHALL gerar em segundo plano um rascunho rotulado do assunto do atendimento anterior, lendo só o período dele; the system SHALL NOT gravar essa nota comum nem o rascunho para protocolo restrito.  <!-- RTK-17 -->

### P1: Contexto do processo após a resposta

**User Story**: Como pessoa que consultou um processo localizado, quero continuar nele depois de cada resposta, sem voltar ao menu geral.

**Acceptance Criteria**:

1. WHEN o sistema responde uma pergunta e o processo da conversa está localizado, the system SHALL oferecer os itens do processo, o atendente e o encerramento, citando o número do processo; IF não há processo localizado, THEN the system SHALL oferecer apenas menu, atendente e encerrar.  <!-- RTK-18 -->

### P2: Segurança e estado

**Acceptance Criteria**:

1. The system SHALL NOT expor dono, fatos nem visibilidade pela API administrativa de protocolos.  <!-- RTK-14 -->
2. WHEN um banco anterior é aberto, the system SHALL migrar as colunas novas sem perder dados, tratar protocolos antigos como não retomáveis e ler fatos de forma defensiva.  <!-- RTK-15 -->
3. The system SHALL NOT registrar telefone de retorno, resumo do atendente nem texto do relato em log ou auditoria.  <!-- RTK-16 -->

## Requirement Traceability

| ID | Task | Status |
| --- | --- | --- |
| RTK-01 | T3, T4 | Done |
| RTK-02 | T1, T4 | Done |
| RTK-03 | T2, T4 | Done |
| RTK-04 | T1, T4 | Done |
| RTK-05 | T1, T4 | Done |
| RTK-06 | T1, T4 | Done |
| RTK-07 | T4 | Done |
| RTK-08 | T4 | Done |
| RTK-09 | T4 | Done |
| RTK-10 | T4 | Done |
| RTK-11 | T4 | Done |
| RTK-12 | T5 | Done |
| RTK-13 | T5 | Done |
| RTK-14 | T1 | Done |
| RTK-15 | T1 | Done |
| RTK-16 | T4 | Done |
| RTK-17 | T4, T5 | Done |
| RTK-18 | T6 | Done |

## Out of Scope

- Anúncio ao usuário do uso de IA (a definir com a área de IA do TJPA); busca de histórico em outros canais; reabrir conversa antiga.
- Janela de 24 h do WhatsApp: retorno após 24 h exige modelo aprovado na Meta (configuração fora do código).
- Ligar o Agent Bot ou criar caixa de entrada.

## Success Criteria

- Com as chaves desligadas, todos os testes anteriores passam sem alteração.
- Cada RTK tem teste offline; resultado de homologação só é afirmado depois de ensaio real.
