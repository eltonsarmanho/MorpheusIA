# Roteiro de atendimento TJPA Validation

**Date**: 2026-10-27
**Spec**: `.specs/features/tjpa-roteiro-atendimento/spec.md`
**Diff range**: árvore de trabalho não comitada sobre `ef7c3ae` (arquivos `M`/`??` do `git status`; `docs/TJPA/` fora do escopo)
**Verifier**: independent sub-agent (author ≠ verifier)
**Iteration**: 3 de 3 (iteração 1: FAIL, 6 sobreviventes e 4 defeitos; iteração 2: FAIL, M33 e M34 sobreviveram)

**Verdict**: PASS

---

## Task Completion

| Task | Status | Notes |
| ---- | ------ | ----- |
| T1 | ✅ Done | - |
| T2 | ✅ Done | - |
| T3 | ✅ Done | - |
| T4 | ✅ Done | Títulos literais afirmados em `RT:114` e `RT:141` |
| T5 | ✅ Done | RTA-06 corrigido (`backend/app/application/chat/handler.py:507-509`) |
| T6 | ✅ Done | - |
| T7 | ✅ Done | Bug das opções após falha corrigido (`handler.py:633-661`) |
| T8 | ✅ Done | - |
| T9 | ✅ Done | - |

---

## Spec-Anchored Acceptance Criteria

Arquivo de teste principal: `backend/tests/integration/test_roteiro_tjpa.py` (RT). Unitário: `backend/tests/unit/test_roteiro_domain.py` (RD).

| Req | Spec-defined outcome | `file:line` + assertion | Result |
| --- | -------------------- | ----------------------- | ------ |
| RTA-01 | Júlia (assistente virtual) + 3 perfis + Atendente + Encerrar | `RT:103-105` - `"Júlia"`, `"assistente virtual"`, `opts[:3] == [PARTE, ADV, GERAL]`, `"✅ Encerrar atendimento" in opts` | ✅ PASS (M35 morto, por `test_rich_flow.py`) |
| RTA-02 | Perfil persistido e travado; heurística não troca | `RT:114`, `RT:117` | ✅ PASS (M32 morto) |
| RTA-03 | Perfil não altera evidência nem equipe | `RT:558-572` (`test_perfil_nao_altera_equipe_nem_evidencia`) - `teams == ["secretaria","secretaria"] and evidences[0] == evidences[1]` | ✅ PASS |
| RTA-04 | Menu geral: Endereço, Telefones, Horário, Balcão, Outra informação + escapes | `RT:141` - `last_opts(gw)[:5] == ["📍 Endereço da unidade", "📞 Telefones e contatos", "🕒 Horário de atendimento", "🏛️ Balcão Virtual", "💬 Outra informação"]` | ✅ PASS (M33 morto) |
| RTA-05 | Pergunta cidade/unidade; só campo cadastrado com fonte; ausente declarado | `RT:156-162` | ✅ PASS |
| RTA-06 | Sem cadastro ou unidade não identificada → RAG; TeleJudiciário só se configurado | `RT:146-147`; `RT:476-489` - `seen == [GERAL_FALLBACK_QUESTIONS["endereco"]]`, `tele in texts` | ✅ PASS (M21 morto) |
| RTA-07 | Sei o número / Sei o CPF / Não sei nenhum | `RT:114` - `last_opts(gw)[:3] == ["🔢 Sei o número", "🪪 Sei o CPF", "❓ Não sei nenhum"]` | ✅ PASS (M34 morto) |
| RTA-08 | Fora do formato → correção; ausente + DV inválido → correção; ausente + válido → não localizado | `RT:171`, `RT:174-175`, `RT:528-533` - `"dígitos de verificação"`, `process_number is None`; `RD:21-23` | ✅ PASS (M01, M02 mortos) |
| RTA-09 | CPF inválido sem busca; válido → indisponibilidade; CPF fora de LLM/log/auditoria | `RT:185`, `RT:187`, `RT:189`; `RT:510-514` - `CPF_OK not in u` em todos os prompts | ✅ PASS (M11, M27 mortos) |
| RTA-10 | Número, classe, participação; 2-5 seleção; >5 refina; nenhum → Vara; nunca por similaridade | `RT:200-204`, `RT:227-229`, `RT:212`, `RT:613-622` - `"Não consegui identificar a opção"`, seleção por "1" | ✅ PASS: `RT:637-646` - `"Você sabe em qual *Vara*" in texts[-1] and choices is None` (M37 morto) |
| RTA-11 | Sabe a Vara → unidade informada + canal; não sabe → TeleJudiciário/humano | `RT:216-217`, `RT:238`, `RT:592-610` | ✅ PASS |
| RTA-12 | Guarda número e unidade; menus por perfil | `RT:125`, `RT:132`, `RT:257` | ✅ PASS |
| RTA-13 | Menu do processo com os itens do roteiro | `RT:550-556` - títulos literais "🗓️ Audiências", "📌 Situação atual", "📝 Pedir diligência", "🔎 Fazer pergunta", "🙋 Falar com atendente"; `RT:412` limites | ✅ PASS (M15, M36 mortos); "Cronologia", "Outro processo", "Encerrar" não nomeados |
| RTA-14 | Audiências/Situação → RAG verificado, aviso do acervo, abstenção sem evidência | `RT:536-547` - `"20/07/2026" in texts[-2] and "não é consulta em tempo real"`, `"Há audiência designada" in seen[0]`, `"situação atual" in seen[1]` | ✅ PASS: `RT:648-660` - sem evidência, `"Não encontrei" in reply and "20/07" not in reply` (teste não passou por mutação própria) |
| RTA-15 | Vazio → pede de novo; acima do limite → resumo sem truncar; senão exato | `RT:250`, `RT:253`, `RT:517-525` - `"passou de 50 caracteres"`, `diligence is None`, `diligence == "x"*50` | ✅ PASS (M04, M05, M28 mortos) |
| RTA-16 | Pergunta o canal com só os disponíveis; sem unidade → direto | `RT:254`, `RT:305`, `RT:385` | ✅ PASS |
| RTA-17 | Nota única antes da transferência; sucesso só após equipe+status; aviso | `RT:257-260`, `RT:352`, `RT:328` | ✅ PASS (M26 morto) |
| RTA-18 | Só número oficial; link com mensagem preenchida; abrir ≠ enviar | `RT:315-317`; `RT:575-590` - `P1 in body and DILIGENCE in body` (query decodificada) | ✅ PASS; ramo "sem número" agora afirma que WhatsApp não é oferecido e Balcão sim, `assigned == []` (`RT:589`) |
| RTA-19 | Link validado/declarado; horário só se cadastrado; diligência em mensagem própria; não afirma recebimento | `RT:283-285`, `RT:297`, `RT:592-598` | ✅ PASS (M25, M31 mortos) |
| RTA-20 | Falha preserva diligência; 3 opções; troca cancela com auditoria; nunca dois canais | `RT:328-339`; `RT:442-450` - `assigned == []`, `AUTOMATION_RESUMED`, `diligence == DILIGENCE`, `"CANCELADO"` em nota, auditoria `"opção diferente"`; `RT:453-458`; `RT:461-466`; `RT:469-473` (texto digitado repete) | ✅ PASS (M19, M20, M23, M24 mortos) |
| RTA-21 | Nota só com campos disponíveis | `RT:257-258`, `RT:377` | ✅ PASS |
| RTA-22 | Idempotência; bloqueio em human_active; encerramento apaga | `RT:363`, `RT:397`, `RT:400`, `RT:427` | ✅ PASS (M29, M30 mortos) |
| RTA-23 | Diligência, CPF e nome fora de log e auditoria | `RT:262`, `RT:189`, `RT:500-507` - `"Maria" not in blob and "Silva" not in blob and "name_search" in blob` | ✅ PASS (M06, M12 mortos) |
| RTA-24 | Menu de continuidade após Balcão e após informações gerais | `RT:287`, `RT:159`, `RT:492-497` - `last_opts == continue_options(False)`, `"Como deseja continuar"` | ✅ PASS (M22 morto) |
| RTA-25 | TKT é número interno | `RT:401` | ✅ PASS |
| RTA-26 | Limites do WhatsApp | `RT:404-415` | ✅ PASS |
| RTA-27 | Identidade centralizada | `RD:100-103` | ✅ PASS |
| RTA-28 | Sem jargão nem alegação falsa | `RD:112-117`; `RT:625-637` (constantes `_TEXT` do handler e textos do orquestrador); `test_persona_cenarios.py:32-38` | ✅ PASS |

**Status**: ✅ 28/28 ACs com evidência `file:line` que bate com a spec

---

## Discrimination Sensor

Cópia isolada em `<scratchpad>/repo/backend` (`docs/processos` por symlink); suíte completa com `-x` por mutante; arquivo restaurado a cada um; cópia apagada ao final.

| # | File | Mutação | Killed? |
| - | ---- | ------- | ------- |
| M01 | `handler.py:323` | handler ignora DV do CNJ | ✅ Killed (`RT:528`) |
| M05 | `handler.py:529-531` | diligência truncada | ✅ Killed (`RT:517`) |
| M11 | `handler.py:190` | sem `mask_cpf` | ✅ Killed (`RT:510`) |
| M12 | `handler.py:396` | nome na auditoria | ✅ Killed (`RT:500`) |
| M14 | `handler.py:621` | wa.me sem diligência | ✅ Killed (`RT:575`) |
| M15 | `flow.py:116` | menu sem Audiências | ✅ Killed (`RT:550`) |
| M19 | `handler.py:646-647` | bug 1: clique de opção após falha sem cancelamento | ✅ Killed (`RT:442`) |
| M20 | `handler.py:646-647` | cancelamento sem `keep_choice` (Balcão) | ✅ Killed (`RT:453`) |
| M21 | `handler.py:509` | RTA-06: unidade não reconhecida sem RAG | ✅ Killed (`RT:476`) |
| M22 | `handler.py:496-498` | RTA-24: fallback sem menu de continuidade | ✅ Killed (`RT:476`) |
| M23 | `handler.py:655-660` | cancelamento sem nota "CANCELADO" | ✅ Killed (`RT:442`) |
| M24 | `handler.py:641-643` | "Outro canal" sem cancelar | ✅ Killed (`RT:320`) |
| M25 | `handler.py:588` | Balcão também transfere | ✅ Killed |
| M26 | `handler.py:754` | nota duplicada em nova tentativa | ✅ Killed |
| M27 | `handler.py:422` | CPF inválido aceito | ✅ Killed |
| M28 | `handler.py:531` | `strip()` na diligência | ✅ Killed |
| M29 | `handler.py:715` | encerramento sem `clear_request()` | ✅ Killed |
| M30 | `domain/handoff.py:27` | bot responde em `human_active` | ✅ Killed |
| M31 | `handler.py:537` | página geral como link direto | ✅ Killed |
| M32 | `orchestrator.py:139` | heurística ignora `profile_locked` | ✅ Killed |
| M33 | `flow.py:87` | `GERAL_MENU` sem "Horário" | ✅ Killed (iteração 3, `RT:141`) |
| M34 | `flow.py:94` | `ID_MENU` sem "Sei o CPF" | ✅ Killed (iteração 3, `RT:114`) |
| M37 | `handler.py:434` | refinamento ainda ambíguo exibe lista em vez de perguntar a Vara | ✅ Killed (`RT:637`) |
| M35 | `flow.py:78` | `PROFILE_MENU` sem advogado | ✅ Killed (`test_rich_flow.py`) |
| M36 | `flow.py:116` | menu do processo sem "Fazer pergunta" | ✅ Killed (`test_rich_flow.py`) |

**Sensor depth**: expandido (25 mutantes)
**Result**: 25/25 killed - PASS ✅ (iteração 3 reexecutou M33, M34, M01, M19, M21, M37; os demais vêm da iteração 2 e o código sob mutação não mudou)
**Isolation**: `git status --porcelain` capturado antes e comparado depois: idêntico.

---

## Code Quality

| Principle | Status |
| --------- | ------ |
| Minimum code / Surgical / No scope creep / Matches patterns | ✅ |
| Spec-anchored outcome check | ✅ |
| Per-layer Coverage Expectation met | ✅ |
| Every test maps to a spec requirement | ✅ |
| Documented guidelines followed: none - strong defaults applied | ✅ |

Observação: as perguntas prontas de Audiências/Situação foram encurtadas (`flow.py`, `PROCESS_QUESTIONS`) para que a busca não abstenha. A versão anterior pedia "local ou modalidade e link de acesso somente se constarem"; essa exigência saiu. A spec (RTA-14) pede perguntas ao RAG com verificação de fundamentação e abstenção honesta, sem exigir local ou link (a afirmação do coordenador de que a spec os exige "quando presentes" não consta do texto de RTA-14). Aceitável: a limitação está declarada em `docs/OPERACAO.md:119`.

---

## Edge Cases

- [x] Diligência só com espaços; acima do limite (`RT:517`)
- [x] Reentrega do evento; nova tentativa sem nota duplicada
- [x] Qualquer opção tocada após falha cancela, não transfere (`RT:442-458`)
- [x] Unidade não reconhecida consulta a base institucional (`RT:476`)
- [x] Refinamento ainda ambíguo (>5) → Vara (`RT:637`)

---

## Gate Check

- **Gate command**: `cd backend && .venv/bin/python -m pytest -q`
- **Result**: 385 passed, 0 failed, 7 deselected (marcadores `live`/`corpus`), 1 warning
- **Test count before feature**: não medido
- **Test count after feature**: 385 (iteração 1: 366; iteração 2: 383)
- **Skipped tests**: nenhum
- **Failures**: nenhuma

---

## Fix Plans

Nenhum aberto. Fix A e Fix B da iteração 2 foram aplicados e verificados.

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status |
| ----------- | --------------- | ---------- |
| RTA-01 a RTA-28 | Implemented | ✅ Verified |

---

## Summary

**Overall**: ✅ Ready (offline; validação em homologação continua pendente)

**Spec-anchored check**: 28/28 ACs
**Sensor**: 25/25 mutations killed
**Gate**: 385 passed

**What works**: todos os pontos de atenção (privacidade de CPF, diligência e nome fora de log e auditoria; nenhum envio aos dois canais; nota única; idempotência; abrir link ≠ envio; nenhum dado inventado; bloqueio em `human_active`) têm asserção e mutante morto.

**Residual (não bloqueia)**: o teste de abstenção de Audiências (`RT:648`) não teve mutante próprio; Audiências/Situação podem omitir local e link (documentado); RTA-01 não afirma o botão "Falar com atendente" nominalmente.

**Next steps**: homologação com o Chatwoot real.
