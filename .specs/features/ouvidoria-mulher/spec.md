# Ouvidoria da Mulher no WhatsApp Specification

Feature incremental sobre `tjpa-roteiro-atendimento`: reutiliza a máquina de estados, o protocolo TKT, a idempotência, a nota
de contexto e a transferência confirmada de `backend/app/application/chat/{flow,handler}.py`. Planejamento e evidências em
`docs/Mulher/Ouvidoria_Mulher_Planejamento.pdf`. Não cria um segundo orquestrador.

## Problem Statement

O menu do WhatsApp não tem o serviço Ouvidoria da Mulher. Quem relata violência precisa de acolhimento, orientações da
Cartilha da Ouvidoria da Mulher do TJPA e de uma triagem curta que entregue ao atendente do Chatwoot, de forma estruturada,
o assunto do atendimento, sem repetir a história e sem pontuar risco. O encaminhamento deve orientar a guardar o protocolo
TKT para eventual atendimento presencial.

## Assumptions & Open Questions

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --- | --- | --- | --- |
| Equipe | `ouvidoria_mulher` | Criada pelo responsável no Chatwoot (2026-11-04) | y |
| Protocolo para atendimento presencial | A mensagem de encaminhamento orienta guardar o TKT | Pedido do responsável (2026-11-04) | y |
| Aprovação do plano | Plano de `docs/Mulher` aprovado para implementar | Mensagem do responsável (2026-11-04) | y |
| Ativação | `OUVIDORIA_MULHER_ENABLED=false` por padrão; ligar só por pedido | Mensagens reais não devem chegar ao serviço sem decisão | y (plano) |
| Contato "prefiro não dizer" | Tratado como não seguro (registro sem retorno por este WhatsApp) | Segurança: só retorna quem confirmou que é seguro | n (agent default) |
| Contato não seguro | Orienta (telefone, e-mail, 180) e, com consentimento, registra o pedido **sem retorno por este WhatsApp** | D3 respondida pelo responsável (2026-11-04) | y |
| Horário da Ouvidoria | 8h às 14h (Belém), todos os dias; dias da semana não verificados | Carta de Serviços do TJPA traz só o horário | n (agent default) |
| Relatos de terceiros | Aceitos | D5 do plano | y (2026-11-04) |
| Orientações | Textos fixos derivados da Cartilha, com página; sem busca livre na Cartilha | PDF da Cartilha ainda não está no repositório; D6 respondida "sim" (aprovação e envio do PDF), a confirmar na prática | n (agent default) |
| Posição no menu e início da implementação | Linha 4, antes de Falar com atendente | D9 "sim" | y (2026-11-04) |
| Retenção do relato | Apagado ao encerrar o protocolo (mesmo mecanismo da diligência) | D7 pendente com a DPO | n (agent default) |
| 190 e 180 | Textos fixos "190" e "180"; WhatsApp do 180 não incluído | Número do WhatsApp do 180 só em fonte secundária | y |

**Open questions:** none blocking; the defaults above are logged and the items marked n await the Ouvidoria (textos, dias, retenção, contatos).

## User Stories

### P1: Entrada e acolhimento

**User Story**: Como pessoa em situação de violência, quero chegar à Ouvidoria da Mulher pelo menu e ver logo os telefones de emergência.

**Acceptance Criteria**:

1. WHEN a chave está ligada, the system SHALL incluir "Ouvidoria da Mulher" no menu principal, com título dentro dos limites do WhatsApp; WHILE a chave está desligada, the menu SHALL permanecer idêntico ao anterior e a opção SHALL ser tratada como texto livre.  <!-- OVM-01 -->
2. WHEN a pessoa escolhe a Ouvidoria da Mulher, the system SHALL responder com acolhimento que cita 190 e 180 e oferecer Perigo agora, Orientações e Falar com a equipe.  <!-- OVM-02 -->
3. WHEN a chave está ligada e uma mensagem livre indica violência (ex.: "meu marido me bate"), the system SHALL oferecer a Ouvidoria e citar 190 e 180, sem iniciar a triagem sozinho.  <!-- OVM-03 -->

### P1: Orientações

**User Story**: Como pessoa que quer entender seus direitos, quero orientações claras da Cartilha.

**Acceptance Criteria**:

1. WHEN a pessoa escolhe Orientações, the system SHALL listar os temas O que é a Ouvidoria, Formas de violência, Assédio, Lei Maria da Penha, Lei do Feminicídio, Como denunciar e Telefones úteis.  <!-- OVM-04 -->
2. WHEN um tema é escolhido, the system SHALL responder com texto fixo que cita a página da Cartilha, sem orientação jurídica individual.  <!-- OVM-05 -->
3. The tema "Como denunciar" SHALL declarar que os passos de RH, sindicato e Delegacia Regional do Trabalho valem para assédio no trabalho.  <!-- OVM-06 -->
4. The tema "Telefones úteis" SHALL mostrar 190, 180 e a Ouvidoria (telefone, e-mail e horário) somente a partir do arquivo de contatos, com a fonte.  <!-- OVM-07 -->

### P1: Triagem estruturada

**User Story**: Como atendente, quero receber o assunto, o ambiente e os sinais de segurança já organizados.

**Acceptance Criteria**:

1. WHEN a pessoa escolhe Falar com a equipe, the system SHALL perguntar, nesta ordem, perigo, para quem, assunto, ambiente, processo ou medida protetiva, contato seguro e relato opcional, e depois pedir consentimento para encaminhar.  <!-- OVM-08 -->
2. Every pergunta SHALL oferecer uma saída ("Prefiro não dizer"; "Outro / não sei" na lista de assunto; "Pular" no número e no relato); WHEN escolhida, the system SHALL gravar `nao_informado` e seguir; "Pular" na etapa do número registra processo existente sem número.  <!-- OVM-09 -->
3. WHEN a pessoa informa perigo, the system SHALL enviar 190 e 180 antes de qualquer outra pergunta e marcar urgência imediata.  <!-- OVM-10 -->
4. WHEN um botão de uma etapa anterior é tocado, the system SHALL ignorá-lo e repetir a pergunta da etapa atual.  <!-- OVM-11 -->
5. WHEN a pessoa informa o número do processo, the system SHALL validá-lo pelo formato e pelos dígitos verificadores do CNJ e gravá-lo como informado, nunca como localizado; número inválido SHALL ser pedido de novo.  <!-- OVM-12 -->
6. IF o relato passa do limite, THEN the system SHALL pedir uma versão menor sem truncar; ELSE SHALL guardá-lo exatamente como recebido.  <!-- OVM-13 -->
7. WHEN a pessoa escolhe Falar com atendente durante a triagem, the system SHALL ir ao resumo com o que já foi coletado.  <!-- OVM-14 -->
8. The system SHALL NOT perguntar CPF, endereço, nome ou dados do agressor, nem calcular escore de risco.  <!-- OVM-15 -->

### P1: Segurança do contato

**User Story**: Como pessoa que pode estar sendo monitorada, quero poder sair rápido e saber se este WhatsApp é seguro.

**Acceptance Criteria**:

1. IF a pessoa diz que o contato não é seguro, prefere telefone ou não responde se é seguro, THEN the system SHALL mostrar telefone, e-mail, horário e 180, seguir a triagem e, só com consentimento, registrar o pedido sem retorno por este WhatsApp: o aviso NOT diz que a equipe continuará por aqui e a nota SHALL ordenar não responder por este WhatsApp.  <!-- OVM-16 -->
2. WHEN a pessoa toca em Retornar ao menu ou digita "sair" em qualquer etapa da triagem, inclusive nas de texto, the system SHALL descartar a triagem e voltar ao menu.  <!-- OVM-17 -->

### P1: Encaminhamento e protocolo

**User Story**: Como atendente, quero a nota completa antes de assumir; como cidadã, quero guardar o protocolo.

**Acceptance Criteria**:

1. WHEN a pessoa confirma Encaminhar, the system SHALL gravar a nota privada de contexto (uma vez) antes de atribuir a equipe `ouvidoria_mulher`, com os rótulos `ouvidoria_mulher`, `humano` e, se houver perigo informado, `urgencia_imediata`.  <!-- OVM-18 -->
2. WHEN a transferência é confirmada (equipe e status), the system SHALL informar o encaminhamento, o protocolo TKT e a orientação de **guardar o protocolo para eventual atendimento presencial**; antes da confirmação NOT afirmar encaminhamento.  <!-- OVM-19 -->
3. WHEN a hora em Belém está fora de 8h às 14h, the encaminhamento SHALL avisar o horário e NOT prometer prazo.  <!-- OVM-20 -->
4. The nota SHALL conter assunto, ambiente, para quem, perigo, processo (marcado "informado, não verificado"), contato seguro, data e hora, protocolo e o relato integral; the nota SHALL registrar que a pessoa foi orientada a guardar o protocolo.  <!-- OVM-21 -->
5. IF a transferência não é confirmada, THEN the system SHALL preservar a triagem e oferecer tentar de novo ou o menu, mostrando telefone e e-mail da Ouvidoria.  <!-- OVM-22 -->

### P2: Privacidade e estado

**Acceptance Criteria**:

1. The system SHALL NOT registrar relato nem número de processo em log ou auditoria.  <!-- OVM-23 -->
2. WHEN o protocolo é encerrado, the system SHALL apagar a triagem do estado.  <!-- OVM-24 -->
3. WHEN o banco anterior é aberto, the system SHALL migrar a coluna `triage` sem perder dados; valores fora da enumeração SHALL ser descartados.  <!-- OVM-25 -->
4. The chatwoot_setup SHALL listar (plano) e, com `--apply`, criar o time, os 3 rótulos e as definições de atributos que faltarem, sem remover nada.  <!-- OVM-26 -->

## Requirement Traceability

| ID | Task | Status |
| --- | --- | --- |
| OVM-01 | T3, T4 | Done |
| OVM-02 | T3, T4 | Done |
| OVM-03 | T3, T4 | Done |
| OVM-04 | T3, T4 | Done |
| OVM-05 | T3, T4 | Done |
| OVM-06 | T3, T4 | Done |
| OVM-07 | T3, T4 | Done |
| OVM-08 | T1, T4 | Done |
| OVM-09 | T1, T4 | Done |
| OVM-10 | T1, T4 | Done |
| OVM-11 | T1, T4 | Done |
| OVM-12 | T1, T4 | Done |
| OVM-13 | T1, T4 | Done |
| OVM-14 | T1, T4 | Done |
| OVM-15 | T1, T4 | Done |
| OVM-16 | T4 | Done |
| OVM-17 | T4 | Done |
| OVM-18 | T4, T5 | Done |
| OVM-19 | T4, T5 | Done |
| OVM-20 | T4, T5 | Done |
| OVM-21 | T4, T5 | Done |
| OVM-22 | T4, T5 | Done |
| OVM-23 | T2, T5 | Done |
| OVM-24 | T2, T5 | Done |
| OVM-25 | T2, T5 | Done |
| OVM-26 | T2, T5 | Done |

## Out of Scope

- Busca livre na Cartilha (depende de ingestão e aprovação humana); aplicação do FONAR; escore de risco.
- Caixa de entrada própria; ligar o Agent Bot; alterar `secretaria` e `gabinete`.
- Anexos, áudio e imagem.

## Success Criteria

- Com a chave desligada, todos os testes anteriores passam sem alteração.
- Os cenários de OVM-01 a OVM-26 têm teste offline; nenhum resultado de homologação é afirmado sem ensaio real.
