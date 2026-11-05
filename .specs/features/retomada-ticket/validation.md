# Validation: FAIL — Retomada por protocolo (RTK-01..RTK-17): sem defeito de código novo, mas 8 mutantes relevantes sobrevivem (RTK-17 e janela do pai)

- **Date**: 2026-11-05 (iteração 3 de 3, final)
- **Spec**: `.specs/features/retomada-ticket/spec.md` (RTK-01..RTK-17), `tasks.md`
- **Diff range**: árvore de trabalho, não commitada (inclui `test_retomada_lacunas.py`, `test_retomada_rodada3.py`, RTK-17, S1, `until`, e a mudança de `_answer`/`process_menu` do coordenador)
- **Verifier**: agente independente (autor != verificador). Nenhum arquivo de `backend/`, `docs/` ou `.specs/` editado além deste relatório. Mutantes e sondas em cópias sob `/tmp/.../scratchpad/verify4/` (apagadas ao final).

## 1. Suíte real

`cd backend && .venv/bin/python -m pytest -q`: **530 passed, 7 deselected, 1 warning** (2,65 s), duas execuções iguais. O número cresceu de 528 para 530 durante a verificação porque o coordenador alterou `_answer` e `test_rich_flow.py` (+2 testes); o código de RTK não mudou nesse intervalo, exceto a linha da janela do pai que usei como controle. Os 7 deselected são `live`/`corpus` (`pytest.ini:8`). Nas cópias de scratch o teste `tests/unit/test_roteiro_domain.py::test_digitos_verificadores_cnj_do_acervo...` foi excluído (precisa de `docs/processos`); o scratch fica em 529 passed + 8 deselected.

## 2. Mudanças do coordenador, verificadas no código

| Mudança | Evidência | Resultado |
| --- | --- | --- |
| S1: tipo explícito de não contato nunca é dono | handler.py:260-275 (`sender_type not in ("contact","")` retorna `None` antes de qualquer fallback) | Corrigido. Mutantes S1, S1a (agent_bot), S1b (user), S1c (agent), S1d (AgentBot) mortos por `test_remetente_explicito_que_nao_e_contato_nunca_vira_dono` |
| Janela superior do protocolo atual | handler.py:632-633 (`until = now + AI_WINDOW_SLACK_S (2 s)`), `_transcript(..., since, until)` | Corrigido. Mutantes sem `until` (Y12) e com folga de 1 h (Y13) mortos |
| RTK-17: nota ao retomar | handler.py:537-548 (`_post_resume_note`), retomada.py:144-149 (`resume_note`) | Funciona; ver lacunas Y02 e Y14 |
| RTK-17: IA do anterior | handler.py:552-570 (`_ai_summary_previous`, claim `aisumr:`) | Funciona; ver lacunas Y05b, Y06, Y07, Y08 |
| `_answer` com menu do processo | handler.py `_answer`: troca `AFTER_ANSWER` por `process_menu` quando há processo | Sem interação com RTK-17 nem com os caminhos da IA: só escolhe as opções do menu depois da resposta; não toca em protocolos, notas, janelas ou contato |

## 3. Evidência por AC

| AC | Testes | Veredito |
| --- | --- | --- |
| RTK-01 | test_retomada_ticket `:92`, `:100` | OK |
| RTK-02 | `:109`, `:555`, `:575`; rodada3 (tipos explícitos, `contact_inbox`, telefone do meta, texto exato de `ticket_sem_contato`) | OK (X2, X5, X6, S1* mortos) |
| RTK-03 | `:118`, `:132`, `:146`, `:155`; lacunas (não sobrescreve, restaura vazio); rodada3 (próprio protocolo digitado) | OK (M23, M24* mortos) |
| RTK-04 | `:155`, `:167`; lacunas (5 estados, triagem) | OK |
| RTK-05 | `:202`, `:218`; lacunas (processo, telefone, sem diligência) | OK |
| RTK-06 | `:229`, `:243`; lacunas (89 dias, aberto, `purge_expired`); rodada3 (laço da API com `asyncio.sleep` trocado) | OK (W11, W12 mortos) |
| RTK-07 | `:251`, `:267`, `:284`, `:300`; lacunas (F, nota da Mulher); rodada3 (falha neutra com frases, link com `no_chat`) | OK (N31, W08 mortos) |
| RTK-08 | `:178`, `:193` | OK |
| RTK-09 | `:307`, `:320`, `:330`, `:344`; lacunas (limite 5, motivo, aberto, link) | OK |
| RTK-10 | `:364`, `:379`, `:404` | OK |
| RTK-11 | `:202`; lacunas (CPF e 600) | OK |
| RTK-12 | `:431`, `:472`, `:482`; lacunas; rodada3 (`no_chat` com mensagens, modelo lento, 30 últimas e atividade, janela superior) | **PARCIAL**: limite inferior da janela do pai no resumo da transferência (W05 sobrevive) |
| RTK-13 | `:443`, `:454`; lacunas (B, C, D); rodada3 ("10" vs "2100") | OK (W09 morto) |
| RTK-14 | `:506` | OK |
| RTK-15 | `:525`, `:540` | OK |
| RTK-16 | `:394`; lacunas; rodada3 (diagnóstico só com formato) | OK (X6 morto agora; N09, N14, N23 mortos na iteração 2) |
| RTK-17 | rodada3 `:27` (nota com anterior, fatos, link, sem transferência), `:38` (restrito sem nota comum), `:50` (IA do anterior sem transferência), `:67` (chave e caso normal do mesmo contato) | **PARCIAL**: 8 mutantes vivos (abaixo) |

## 4. Sensor de discriminação (iteração 3)

44 mutantes executados sobre a árvore atual: **32 mortos, 12 vivos (8 relevantes, 4 equivalentes)**.

**Os 14 sobreviventes da iteração 2:** 13 mortos (M23, N02, N10, N11, N12, N31, W08, W09, W11, W12, X2, X5, X6), todos por `test_retomada_rodada3.py`. **Um continua vivo: W05** (limite inferior da janela do pai no resumo da transferência, `handler.py:633`; os testes novos cobrem só a janela do anterior de RTK-17).

**S1 e variantes:** S1 e as quatro por tipo mortos (S1e é equivalente, ver abaixo).

**Novos para RTK-17 e `until`:** mortos — nota também para caso restrito (Y01), IA do anterior sem `since` (Y03), sem `until` (Y04), nota pública (Y09), IA sem a chave (Y10), rascunho público (Y11), janela atual sem `until` (Y12), folga de 1 h (Y13), nota sem fatos (Y15), rascunho sem rótulo do anterior (Y16), lê a conversa atual (Y17), lê sem máscara/sem filtro (Y18), nota não gravada (Y19), nota gravada antes de verificar o dono (Y20).

### Sobreviventes relevantes (8)

| Mutante | Falha injetada | Por que sobrevive | AC |
| --- | --- | --- | --- |
| W05 | resumo da transferência lê o pai desde antes do `opened_at` dele | nenhum teste com mensagem anterior ao pai na conversa do pai, para o resumo da transferência | RTK-12 |
| Y02 | nota de retomada com o link mesmo que a conversa anterior hospede protocolo restrito/sem chat | nenhum teste de link na nota de RTK-17 (sonda T1: o código atual omite o link corretamente) | RTK-17 |
| Y05b | a IA do anterior roda mesmo se o protocolo atual não é normal | nenhum teste com atual restrito retomando um normal com a IA ligada (sonda T2: o código atual envia 0 prompts) | RTK-17 |
| Y06 | rascunho postado mesmo se o protocolo atual deixou de ser normal durante a chamada | corrida não testada | RTK-17 |
| Y07 | rascunho postado mesmo se o protocolo aberto mudou durante a chamada | corrida não testada | RTK-17 |
| Y08a | chave de claim global (`aisumr`): só o primeiro rascunho do sistema inteiro | nenhum teste com duas retomadas em protocolos diferentes | RTK-17 |
| Y08b | chave do rascunho do anterior igual à da transferência (`aisum:`): um dos dois é descartado | nenhum teste com retomada e transferência com IA no mesmo protocolo | RTK-17 |
| Y14 | nota de retomada sem "(ainda aberto)" | o teste da nota não cobre protocolo anterior aberto | RTK-17 |

### Equivalentes (não contados)

- **S1e**: mutação sem efeito (a guarda de tipo explícito já retornou antes).
- **Y05a**: tirar `found.visibility == "normal"` da guarda da IA do anterior: o protocolo restrito sai antes (handler.py `_resume_lookup`, ramo restrito), então a condição é código morto nesse ponto.
- **Y05c**: tirar `found.contact_id == current.contact_id`: o dono do protocolo anterior já foi provado igual ao contato do evento em `_resume_lookup`; com a guarda removida a IA ainda lê só protocolo do mesmo contato. O efeito é só rodar também quando o atual não tem dono gravado.
- **Y21**: tirar `llm is not None`/`hasattr(list_messages)`: o erro vira `ai_summary_previous_failed` na auditoria (capturado em `_ai_summary_previous`), sem efeito para a pessoa.
- Do round 1/2, sem mudança de código: M05b, M15/b/c, M27, M39, M45, M46, M57, M59, N07, N21, N27, W16 (`>>>` não neutralizado; o marcador de fechamento exige `<<<`), W20 (pai com dono apagado pela retenção), X4.

## 5. Vazamentos revisados (restrito/no_chat; prompt, notas, listas, auditoria, logs)

Sem vazamento de conteúdo restrito ou sem chat encontrado nos caminhos testados e sondados:

- **Nota de retomada (RTK-17).** Gravada só na conversa da mensagem recebida (`conv_id` do evento), depois de `_resume_lookup` provar que `found.contact_id == contact` e que o protocolo não é `no_chat`; caso restrito retorna antes (sem nota comum, Y01 morto). Conteúdo: `history_line` do anterior (id, equipe, motivo, unidade informada, processo informado, resumo do atendente, link). Link omitido quando a conversa hospeda protocolo não normal (sonda T1). Não chega à conversa de outro contato: o destino é o `key` da própria mensagem.
- **IA do anterior.** Lê só `found.conversation` no período `[opened_at, closed_at]` do protocolo normal do mesmo contato; mensagens privadas e sem `created_at` ficam de fora; CPF/telefone/e-mail mascarados e delimitadores neutralizados pelo mesmo `_transcript`. Não roda para o atual restrito (sonda T2: 0 prompts). Postagem só como nota privada na conversa atual e só se o atual continua aberto e normal.
- **Prompt do resumo da transferência.** Janela do atual agora com limite superior; janela do pai com `opened_at`/`closed_at`; conversa com vários protocolos coberta por lacunas e rodada3.
- **Auditoria e logs.** `resume_vinculado` e `ai_summary_previous_*` só gravam ids de protocolo; `ticket_sem_contato` só o tipo (20 caracteres) e os nomes das chaves; falhas de IA gravam só o nome da exceção.

Observações, sem vazamento:

1. **Segunda retomada (sonda T3).** Retomar um segundo protocolo na mesma conversa grava uma segunda nota "continua o B" enquanto o `parent_ticket_id` fica no primeiro (A) e o rascunho da IA só sai para o primeiro (claim por protocolo atual). Inconsistente, não vaza.
2. **Atual restrito retomando um normal.** A nota comum com os fatos do normal é gravada na conversa restrita (sonda T2). A spec proíbe a nota para o protocolo *retomado* restrito, não para o *atual* restrito; vale decidir.
3. **Ids de conta.** O resumo do anterior usa o `account` do evento com o id de conversa do protocolo anterior (instalação com uma única conta).

## 6. Lacunas de precisão da spec

1. RTK-13: numerais por extenso não são conferidos.
2. RTK-17 não diz o que acontece se o protocolo *atual* é restrito (observação 2) nem na segunda retomada na mesma conversa (observação 1).
3. RTK-09 "últimos 5": o pai pode somar uma sexta linha.
4. RTK-05: o resumo do atendente só tem o CPF mascarado.
5. RTK-17 e RTK-12 não definem a janela quando o protocolo anterior ainda está aberto (`closed_at` nulo => sem limite superior; o protocolo aberto é o último da sua conversa, portanto sem risco hoje).

## 7. Lacunas ordenadas

1. **RTK-17 (mata Y02, Y05b, Y08a, Y08b, Y14):** testes de link omitido na nota quando a conversa anterior hospeda restrito/`no_chat`; atual restrito + retomada de normal + IA ligada => nenhum prompt; duas retomadas em protocolos distintos => dois rascunhos; retomada + transferência com IA no mesmo protocolo => ambos os rascunhos; protocolo anterior aberto => "(ainda aberto)".
2. **RTK-17 corridas (Y06, Y07):** gateway que, durante `list_messages`, torna o atual restrito ou fecha-o; afirmar que nada é postado.
3. **RTK-12 (W05):** mensagem anterior ao `opened_at` do pai, na conversa do pai, no resumo da transferência.
4. Decisão de produto: nota de retomada em protocolo atual restrito; segunda retomada na mesma conversa.

## Veredito

**FAIL.** Todas as correções pedidas estão no código e verificadas (S1, `until`, RTK-17 sem vazamento, fallback de contato, `_answer` sem interação), 13 dos 14 sobreviventes anteriores foram mortos e não encontrei defeito de código novo. Mas a regra do gate é rígida: oito mutantes relevantes continuam vivos, sete deles em RTK-17 (cobertura parcial: link, atual restrito, corridas, claim, "ainda aberto") e um em RTK-12 (W05), de modo que RTK-12 e RTK-17 não têm teste que discrimine tudo. Para PASS: fechar a lista da seção 7 (testes apenas; o código atual já se comporta corretamente nas sondas T1 e T2).

## Pós-veredito da iteração 3 (autor, 2026-11-05)

O limite de 3 rodadas de verificação foi atingido; o veredito independente **permanece FAIL**. Não houve defeito de código nem vazamento: os 8 mutantes relevantes que sobraram eram lacunas de teste.

O autor fechou essas lacunas em `backend/tests/integration/test_retomada_rodada4.py` (7 testes) e confirmou, em cópia isolada, que os mutantes Y02, Y05b, Y06, Y07, Y08a, Y08b e W05 passam a ser mortos (Y14 é coberto pela asserção de "(ainda aberto)"). Suíte: 537 passando.

**Isto não é uma quarta verificação independente.** Os testes novos foram escritos e conferidos pelo próprio autor. Para o gate de conclusão (`validate_state.py`) a feature segue sem PASS até uma nova rodada de verificação independente ou decisão explícita do responsável.

Decisões de produto pendentes (apontadas pelo Verifier):
1. Protocolo atual restrito que retoma um normal recebe a nota comum de retomada com os fatos do normal (a spec só proíbe a nota quando o retomado é restrito).
2. Uma segunda retomada na mesma conversa grava uma segunda nota, mas `parent_ticket_id` continua no primeiro, e o rascunho de IA cobre só o primeiro.
3. Números escritos por extenso ("dez dias") não passam pela conferência de números do resumo por IA.
