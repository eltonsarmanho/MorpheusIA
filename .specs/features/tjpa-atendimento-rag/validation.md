# Atendimento omnichannel com Hybrid RAG (piloto TJPA): relatório do Verificador (iteração 3 de 3)

**Date**: 2026-10-09
**Spec**: `.specs/features/tjpa-atendimento-rag/spec.md`
**Diff range**: `a9cc4e0..27f963f` (5 commits: 9dba3b7, 4962791, d7353fd, 8da15c5, 27f963f) MAIS a árvore de trabalho não commitada (20 arquivos em `backend/`, +316/-48, e arquivos novos não rastreados: `backend/tests/integration/test_chatwoot_client_and_setup.py`, `backend/tests/eval/test_gates.py`, `backend/evals/heldout*.yaml`). O que foi verificado é a árvore de trabalho, não o HEAD. Durante a verificação, terceiros (não este verificador) alteraram `backend/app/infrastructure/chatwoot/client.py`, `backend/app/interfaces/chatwoot_setup.py` e `deploy/docker-compose.yml` (11:29 a 11:37); o sensor e as sondas rodaram sobre uma cópia tirada antes dessas edições, e a suíte offline na árvore real passou a 143 testes verdes depois delas (eram 135 na cópia verificada).
**Verifier**: sub-agente independente (autor diferente do verificador)



**Iteração 3 (final)** (2026-10-09, HEAD `2b1ef7b`; correções em `ef8cab6`, testes em `850c14a`). Sensor e sondas refeitos em cópia NOVA do HEAD. As seções "iteração 2" e "iteração 1" abaixo são histórico.

## Validation: FAIL ❌ (iteração 3)

**Result**: FAIL. O estado melhorou muito: todos os defeitos Major e as três regressões da iteração 2 foram corrigidos e provados por sonda, e a suíte tem 253 testes verdes. Ainda não há PASS por quatro motivos, nenhum deles Blocker: (1) T16 segue aberta; (2) B13 (retomada ineficaz com responsável humano) é limitação conhecida e não corrigida; (3) a fundamentação ainda deixa passar artigo com separador de milhar e unidades fora da lista; (4) 15 mutantes sobreviventes reais, inclusive 2 que a equipe declarou cobertos (N105 e N109).

### O que impede o PASS (em ordem)

1. **T16 aberta** (`tasks.md:370`): a verificação manual do console no navegador não foi feita; UI-01 só está provado no nível da API (`test_api.py:75-77`).
2. **B13, CHW-04**: `handler.py:127-130` põe a conversa em `human_active` e silencia o bot se `meta.assignee` continua preenchido após a retomada. A correção nova (`handler.py:249-252`, status volta a `pending`) não remove o responsável. Sonda P11 continua: `silent`, `human_active`, 0 respostas. Se o Chatwoot real mantém o responsável após resolver (comportamento a confirmar), o comando de retomada fica sem efeito. Opção: desatribuir a conversa na retomada, ou ignorar o responsável quando o estado é `automation_resumed`.
3. **RAG-06**: `grounding.py:123-127` captura só `(\d+)` depois de "art."; "art. 1.016" é lido como "1", que existe em "art. 1.015". Sonda S6: evidência "art. 1.015", resposta "art. 1.016", resultado aprovado. Também passam "3 semanas" e "duas semanas" contra "15 dias" (unidade `semanas` fora de `_QUANTITY`). Correção: capturar `\d+(?:\.\d{3})*` no artigo e comparar sem pontos; incluir semanas e demais unidades.
4. **15 lacunas reais de teste** (tabela abaixo).
5. **COL-02**: o coletor ainda não preenche `valid_from` e `valid_until` (`collection/service.py` só consome `valid_until` em `:146-148`); a vigência fica só como `validity_flag`.

### Gate (iteração 3)

- `cd backend && .venv/bin/python -m pytest -q`: 253 passed, 7 deselected (`corpus`), 1 warning, ~1 s. O gate Build não foi executado (proibido).
- `tasks.md`: T15, T17 e T18 agora marcadas feitas; só `:370` (T16) está aberta. As evidências de Chatwoot real, MariTalk e VM (`docs/AVALIACAO.md`) não podem ser reproduzidas por este verificador (sem rede) e ficam como não verificadas por mim.

### Sensor (iteração 3)

188 execuções de mutantes em cópia nova: 162 mortos, 26 sobreviventes. 11 são equivalentes ou redundantes (`knowledge_store.py:71`, `:554`, `:575`, `:374`, `:351`, resíduos de FTS e vetores em `knowledge_store.py`, `api/main.py:26`, `collection/service.py:102` agora redundante com a decisão `stale` gravada, `orchestrator.py:64` e `:66` limites de tamanho do aceite que a regra "todas as palavras" torna irrelevantes). **15 lacunas reais**:

| Mutante | File:line | Mutação | Observação |
| ------- | --------- | ------- | ---------- |
| N105 | `app/application/ingestion/service.py:294` | capa oculta só documentos `RESTRICTED` | declarado coberto, mas o teste `test_verifier_round2.py:121-130` não discrimina (o nome "Decisão" repete em outro documento aprovado) |
| N109 | `app/application/chat/handler.py:180` | `retry_handoff` aceita qualquer estado | declarado coberto; `test_verifier_round2.py:161-165` não o mata. Quase redundante: sem o guarda, a máquina de estados ainda levanta `InvalidTransition`, mas só depois de zerar as tentativas e gravar a auditoria `handoff_retry_requested` (efeito colateral sem teste) |
| Q02e | `app/application/answering/orchestrator.py:221` | processo deduzido por termos exclusivos não é passado ao retriever | a resposta traria trechos de outros processos sob o aviso "Considerei o processo X" |
| Q03 | `app/infrastructure/sqlite/knowledge_store.py:469` | `processes_with_term` sem `_ELIGIBLE` | termos de documentos pendentes contariam como exclusivos |
| Q01b | `knowledge_store.py:385` | decisão de sistema só para `STALE`, não para `NEEDS_REVIEW` | recoleta idêntica de documento em divergência poderia voltar `approved` sem teste que o impeça |
| Q02b | `orchestrator.py:210` | termo de 1 a 3 letras passa a identificar processo | |
| Q05, Q05d | `app/application/answering/intent.py:21,20` | "falar com um atendente" sem "quero"; "quero falar com uma pessoa/alguém/servidor" | padrões sem teste próprio |
| Q07b | `app/infrastructure/web/fetcher.py:44` | `robots.txt` seguido para outro host | |
| N35b | `fetcher.py:70` | redirect para `http` aceito | |
| N35c | `fetcher.py:62` | robots do destino do redirect não reavaliado | |
| N115 | `fetcher.py:61` | limite de 4 saltos | |
| Q04b | `orchestrator.py:64` | aceite ignora o `?` | |
| Q08b, Q08c | `handler.py:252,251` | falha ao voltar o status `pending` na retomada: auditoria e proteção | sem teste |

Mortos que antes sobreviviam e agora são cobertos: M27, M112, M119 (RRF e reformulação), N98c, N100, N103, N103b, N104, N110, N113, N12d, M02, M15 a M17, M26, M33, M36, M37, M42, M43, M49, M52 a M54, M73, M82, M84 a M88, M96, M97, M116, M118, M127, M131 a M134, M143 a M147 e os novos Q01, Q02, Q02c, Q02d, Q02f, Q04, Q05b, Q05c, Q06, Q06b, Q06c, Q07, Q08.

### Achados das iterações anteriores, sondas reexecutadas

| Achado | Estado | Evidência |
| ------ | ------ | --------- |
| B2 e S6 (fundamentação) | ⚠️ quase | `grounding.py:106-135`: valor exato, quantidade, data por extenso, artigo, numeral por extenso. Sondas: "art. 99" vs 98, "dez dias" vs 5, "4 réus" vs 3, "20 salários", "3 anos" agora reprovam; 11 respostas legítimas continuam aprovadas. Resíduos: item 3 acima |
| B3 / R-D (reaprovação) | ✅ corrigido | `knowledge_store.py:385-391` grava decisão `stale`/`needs_review`; sonda: aprovar, vencer, recoletar duas vezes devolve `pending_review`, `pending_review`, `pending_review` |
| B13 | ❌ persiste | ver item 2 |
| R-A (aceite frouxo) | ✅ corrigido | `orchestrator.py:57-67`; sonda: "quero saber o horário" e "pode informar o horário" não transferem; "sim", "pode", "ok", "sim por favor encaminhe" transferem |
| R-B (falsos positivos) | ✅ corrigido | `intent.py:17-26`; sonda: as 5 perguntas legítimas voltam a ser `consulta_processual`; pedidos reais reconhecidos. Resíduos baixos: "me coloca com uma pessoa" não é reconhecido; "o juiz mandou chamar o oficial para uma pessoa" é falso positivo contrived |
| R-C (ORQ-03) | ✅ corrigido | `orchestrator.py:179-186,208-221`; sonda: dois processos parecidos pedem esclarecimento; termo exclusivo (`decurso`, `manifestação`) deduz o processo com aviso e sem gravar `st.process_number` (o turno seguinte ambíguo volta a pedir esclarecimento). Observação: com um único processo no acervo, uma pergunta sem identidade ainda pede esclarecimento (o spec só exige com mais de um candidato): Low |
| R-E (robots 301) | ✅ corrigido | `fetcher.py:42-45`; sonda: coleta segue com status 200 |
| R-G (mudo após esgotar) | ✅ aceito como desenho | auditoria `handoff_exhausted`; reabre por `retry-handoff` |
| B12 residual (pendente vs pendente) | ❌ persiste, Low | `knowledge_store.py:336-352`; sonda R14: se só o segundo de dois documentos pendentes idênticos for aprovado, o trecho dele é duplicata e não é indexado |

### Regressões novas (diff `1fc9c14..2b1ef7b`)

Nenhuma de gravidade Medium ou maior. Pontos de atenção Low:
- `orchestrator.py:208-221`: a dedução por termo exclusivo usa o corpus indexado; um termo incidental exclusivo de um processo (por exemplo um nome comum) pode escolher esse processo sem ambiguidade real. O aviso "Considerei o processo X" mitiga e a mutação Q02e mostra que nenhum teste prova que o filtro chega ao retriever.
- `handler.py:249-252`: `resume_automation` chama o gateway fora do lock da conversa, como antes para o estado; uma falha é só auditada.
- `docs/AVALIACAO.md` e `tasks.md` declaram T17 e T18 concluídos com base em execuções que este verificador não reproduz.

### ACs (iteração 3)

46 de 51 PASS, 5 PARCIAL, 0 sem teste. Os parciais: RAG-06 (item 3), COL-02 (item 5), CHW-04 (item 2), CHW-12 (critério estrutural, sem teste direto), UI-01 (item 1). Passaram a PASS nesta rodada: ING-01 e ING-10 (`test_ingestion_pipeline.py:127-137`, `pages == 5` e totais), RAG-01 (M27 e M119 mortos), COL-05 (R-D), ORQ-01, ORQ-03, ORQ-04, CHW-02.

### Resumo da iteração 3

**Overall**: ❌ Not Ready para PASS (iteração 3 de 3; sem iteração 4 prevista, o restante passa ao responsável pela feature)
**Sensor**: 162 de 188 mortos; 15 sobreviventes reais.
**Gate**: 253 passed, 0 failed.
**Para chegar a PASS**: (a) executar T16; (b) decidir e implementar o tratamento de B13; (c) fechar o buraco do artigo com milhar e as unidades de tempo; (d) escrever testes que matem N105, N109, Q02e, Q03, Q01b e os casos de intent Q05/Q05d; (e) preencher `valid_until` no coletor ou retirar COL-02 "vigência" do escopo; (f) rodar o gate Build com o índice real.

---

**Iteração 2** (2026-10-09, HEAD `1fc9c14`; correções em `0164eca`, testes em `bebb461`). O sensor e as sondas foram refeitos em cópia NOVA da árvore atual; os achados abaixo valem para essa árvore. A seção "Iteração 1" mais adiante é o histórico.

## Veredito da iteração 2: FAIL ❌ (histórico)

Síntese da iteração 2: a maior parte dos achados foi corrigida e provada por sonda (B1, B2 parcial, B4 a B12, B14 a B17), mas restam 1 defeito Major, 3 regressões introduzidas pelas correções, 1 achado antigo não tratado e 19 mutantes sobreviventes reais, dois deles por testes vazios.

### Gate (iteração 2)

- `cd backend && .venv/bin/python -m pytest -q`: 206 passed, 7 deselected (`corpus`), 1 warning, ~1 s. O gate Build (`-m corpus`, `evals.run_eval`) não foi executado (proibido).
- T15 a T18 continuam abertos em `tasks.md:352,370,388,405,406`; `docs/AVALIACAO.md` agora existe, mas a tarefa T17 não está marcada.

### Sensor (iteração 2)

169 execuções de mutantes em cópia nova (142 mortos, 27 sobreviventes). 8 sobreviventes são equivalentes ou redundantes por defesa em profundidade (`knowledge_store.py:71` sem `active=1`, `:532` e `:553` filtros de domínio redundantes entre si, `:374` e `:351` `_drop_index` e `active` redundantes, `api/main.py:26`, resíduos de FTS e vetores em `knowledge_store.py`). Sobram **19 lacunas reais**, todas com teste ausente ou vazio:

| Mutante | File:line | Mutação | Observação |
| ------- | --------- | ------- | ---------- |
| M27 / N116 | `app/application/retrieval/hybrid.py:105` | `rrf_k` padrão do retriever 60 para 10 | `test_rrf_usa_k_60_por_padrao...` (`test_verifier_regressions.py:310-312`) testa a função, não o retriever |
| M119 | `hybrid.py:147` | RRF só com a lista vetorial | o teste `:312` não passa pelo `HybridRetriever` |
| M112 | `orchestrator.py:220` | segunda tentativa roda mas seu resultado é ignorado | `:358` só afirma que a 2ª chamada usa a pergunta reformulada |
| N103 | `handler.py:117` | eco do bot não reconhecido (`has_event`) | TESTE VAZIO: `test_verifier_regressions.py:235-244` usa para o eco o mesmo id (1) da mensagem de entrada, que já foi reivindicado como `msg:1:77:1`, então o eco vira `duplicate` e nunca chega ao ramo corrigido. A sonda R6 com ids distintos passa, mas nenhum teste a cobre |
| N103b | `handler.py:254` | id da mensagem do bot não registrado | idem |
| N104 | `handler.py:71` | status sem `updated_at` volta a ser deduplicado | TESTE VAZIO: `:248-252` só envia UM `resolved`; o cenário "segundo resolved" (nome do teste) nunca roda |
| N105 | `ingestion/service.py:294` | capa oculta só documentos `RESTRICTED` | `:256-262` só usa o contracheque (que é `RESTRICTED`); falta caso pendente por sigilo no texto ou sem texto |
| N100 | `handler.py:208` | aviso "Encaminhei" volta a ficar sem proteção | sem teste de falha do envio do aviso |
| N98c | `handler.py:182` | `retry_handoff` não zera tentativas | o teste `:136-149` passa porque `fail_assign` zerou |
| N109 | `handler.py:180` | `retry_handoff` aceita qualquer estado | `:152-157` só tenta no estado `bot_active` |
| N110 | `handler.py:196` | transferência confirmada só por equipe (sem status `open`) | CHW-10 |
| N113 | `knowledge_store.py:457` | `approved_process_numbers` sem `_ELIGIBLE` | esclarecimento pode listar processo sem documento aprovado |
| N12d | `grounding.py:112` | quantidade sem limite de palavra | `15` casaria `115` |
| N35b | `fetcher.py:66` | redirect para `http` aceito | |
| N35c | `fetcher.py:58` | robots do destino do redirect não reavaliado | |
| N115 | `fetcher.py:57` | limite de 4 saltos vira 400 | |
| N69c | `orchestrator.py:212` | nome próprio deixa de contar na identidade do processo | |
| N92b, N92c | `orchestrator.py:64` | aceite sem checar negação; aceite com até 60 palavras | o filtro de aceite não é discriminado (ver regressão R-A) |

### Estado dos achados da iteração 1 (sondas reexecutadas na árvore atual)

| Achado | Estado | Evidência |
| ------ | ------ | --------- |
| B1 domínio na mesma conversa | ✅ corrigido | `hybrid.py:119-120`; sonda: 2ª pergunta institucional devolve `answer`; teste `test_verifier_regressions.py:43` |
| B2 fundamentação numérica | ⚠️ corrigido em parte | `grounding.py:98-121`: valor por igualdade, quantidade com unidade, data por extenso, id com limite. Sondas: R$ 1.000,00 vs 11.000,00, 15 vs 5 dias, 30 vs 15 dias, 21 vs 20 de julho e 11h vs 10h agora reprovam, e 6 respostas legítimas continuam aprovadas. Resíduos que ainda passam: "art. 99" contra "art. 98" (3 a 4 dígitos sem unidade), "dez dias" contra "5 dias" (numeral por extenso), "4 réus" contra "3 réus" (unidade fora da lista `_QUANTITY`) |
| B3 reaprovação de `stale` | ❌ persiste | ver R-D abaixo |
| B4 SHA antes de indexar | ✅ corrigido | `ingestion/service.py:170` + `knowledge_store.py:187`; sonda: nova execução devolve `ok` com 4 trechos indexados; teste `:104` |
| B5 exceção sem resposta | ✅ corrigido | `orchestrator.py:119-128`, `handler.py:99-103`; sonda: resposta técnica enviada, `ia_falha` aplicada; testes `:117`, `:132` |
| B6 limite de tentativas | ✅ corrigido | `handler.py:169-173,214-220`; sonda: 3 tentativas, 1 aviso final, silêncio depois; teste `:143-149` (lacuna N98c) |
| B7 falso positivo "transferência" | ⚠️ trocado por outros | o caso "transferência de valores" não dispara mais, mas ver R-B |
| B8 PII em título e `ctx` | ✅ corrigido | `ingestion/service.py:245`; sonda: `Petição enviada por [EMAIL]`; teste `:181` |
| B9 aviso de transferência | ✅ corrigido | `handler.py:206-212`; sonda: `handoff`, estado `human_active`, sem exceção; sem teste (N100) |
| B10 redirect | ✅ corrigido | `fetcher.py:55-76`; sonda: só `centralservicos.tjpa.jus.br` é contatado; teste `:199` |
| B11 trechos curtos | ✅ corrigido | `text_processing.py:12`; sonda: `['Defiro.']`, `['Cite-se a ré.']`; teste `:216-217` |
| B12 duplicata contra pendente | ✅ corrigido (aprovado vs pendente) | `knowledge_store.py:336-352`; sonda: o aprovado fica indexado; teste `:231`. Entre dois pendentes o segundo continua sendo duplicata do primeiro (se só o segundo for aprovado, perde o índice): Low |
| B13 retomada com responsável persistente | ❌ persiste (não estava na lista de correções) | `handler.py:127-130`; sonda P11: `silent`, estado `human_active`, 0 respostas |
| B14 eco do bot | ✅ corrigido | `handler.py:117,254`; sonda R6 com ids distintos: `ignored`, bot segue ativo. Sem teste efetivo (N103) |
| B15 status sem `updated_at` | ✅ corrigido | `handler.py:68-72`; sonda R7: segundo `resolved` sem carimbo fecha a conversa. Sem teste efetivo (N104) |
| B16 capa e nomes retidos | ✅ corrigido | `ingestion/service.py:294-295`; sonda R8: documento pendente por sigilo no texto aparece como "acesso em revisão". Teste só cobre o caso restrito (N105) |
| B17 SEC-02 | ✅ corrigido | `handler.py:156-157`; sonda: auditoria `['injection_flagged', 'reply:answer']`; teste `:271` |

### Regressões e defeitos novos (em ordem de gravidade)

**R-D [Major] B3 persiste na segunda recoleta (COL-05).** `collection/service.py:100-104` só ignora a decisão quando o estado anterior é `STALE`. A 1ª recoleta grava o documento como `pending_review` (o estado `STALE` some), e a 2ª recoleta com o mesmo conteúdo reaplica `lookup_decision` e devolve `approved` sem revisor. Sonda: aprovar, `refresh_staleness(+400d)`, recoletar (`pending_review`), recoletar de novo: `approved`. Qualquer `collect_all` repetido republica conteúdo vencido. O teste `:78-83` só faz a 1ª recoleta. Correção: invalidar a decisão no momento em que o documento vira `stale` (por exemplo registrar uma decisão `stale` em `curation_decisions` ou comparar `collected_at`).

**R-A [Medium] Aceite do encaminhamento ficou frouxo demais.** `orchestrator.py:57-65`: `_is_affirmative` aceita qualquer mensagem de até 6 palavras que contenha uma de `sim s pode quero claro ok certo por favor...` e nenhum "não" nem "?". Com `offer_pending` verdadeiro (após qualquer abstenção), "quero saber o horário", "pode informar o horário" e "ok, qual o horário do balcão" são tratadas como aceite e disparam transferência humana em vez de responder. Era `fullmatch` na iteração 1. As mutações N92b e N92c sobrevivem. Correção: exigir que a mensagem inteira seja composta de palavras de aceite.

**R-C [Medium] ORQ-03 relaxado e processo "fixado" por inferência.** `orchestrator.py:177,199-203,208-213`: sem número de processo, só pede esclarecimento se faltar identidade (menos de 2 termos específicos e sem nome próprio); com 2 ou mais termos específicos, assume o processo do trecho mais bem classificado e o grava em `st.process_number`. Sonda: dois processos com decisão quase igual; "Qual decisão determinou a suspensão para suspender a cobrança do contrato bancário?" responde pelo processo 1 (com aviso "Considerei o processo...") sem perguntar, o que contraria "há mais de um candidato THEN pedir esclarecimento"; no turno seguinte, "Existe certidão de decurso de prazo sem manifestação da ré?" (que só existe no processo 2) abstém com `baixa_similaridade_semantica`, porque o filtro ficou preso ao processo 1.

**R-B [Medium-Low] Novos falsos positivos de pedido de atendente.** `intent.py:18-22`: `(falar|conversar|chamar|passar|...|encaminhar|encaminhe).{0,25}(atendente|humano|pessoa|alguem|servidor)` e `(quero|preciso|...).{0,15}(pessoa|...)`. Sonda: 5 de 6 perguntas processuais legítimas viram `atendimento_humano`: "Quando devo chamar a pessoa citada para a audiência?", "Preciso saber se a pessoa jurídica foi citada", "Quero saber o que a pessoa disse na audiência", "A decisão mandou encaminhar os autos ao servidor?", "O juiz determinou passar os autos para a pessoa responsável?". "Pessoa" e "servidor" são vocabulário comum do processo.

**R-E [Low] `robots.txt` com redirecionamento suspende a coleta.** `fetcher.py:42-48` + `follow_redirects=False`: um 301/302 no `robots.txt` (por exemplo para o host canônico) cai no ramo "indisponível (HTTP 301); coleta suspensa". É falha segura, mas pode inviabilizar uma fonte real. Sonda R5.

**R-F [Low] B13 (ver tabela).** A retomada continua ineficaz quando a conversa mantém responsável humano.

**R-G [Low] Esgotadas as tentativas, o bot fica mudo.** `handler.py:169-173`: em `handoff_requested` com o limite atingido, as mensagens seguintes recebem `silent` até o comando `retry-handoff`. Há auditoria `handoff_exhausted` e o aviso final, mas nenhum alerta ativo à equipe além das etiquetas `ia_falha` e `humano`.

**Testes vazios ou fracos introduzidos:** `test_verifier_regressions.py:235-244` (eco com id repetido, vira `duplicate`), `:248-252` ("segundo resolved" com um só evento), `:256-262` (só caso restrito), `:310-312` (testa a função, não o retriever), `:358` (não prova que o resultado da 2ª tentativa é usado).

### ACs: mudanças em relação à iteração 1

- Passaram de PARCIAL a PASS: SEC-02 (`test_verifier_regressions.py:271`), ING-06 (`:221`), ING-09 (`:104,508`), ING-03 (`:533`), ING-08 (`:518`), CUR-03 (`:287-300`, `:284`), RAG-05 (`:543`), RAG-03 (`:277-278`), RAG-09 (`:343`), ORQ-04 (`:367,382`), CHW-03 (`:393`, `:143-149`), CHW-04 (`:420`), CHW-05 (`:431`), CHW-06 (`:459`), CHW-09 (`:375,439`), CHW-10 (`:411`), COL-06 (`:491,495`), COL-07 (`:462-470`), SEC-01 (`:303`; título e `ctx`: `:181`).
- Continuam PARCIAL (11): ING-01 (contagem de páginas não afirmada), ING-10 (totais do relatório), RAG-01 (M27, M119: `hybrid.py:105,147`), RAG-06 (resíduos de B2), COL-02 (o coletor ainda não preenche `valid_from` e `valid_until`; `:471-478` só prova o consumo do campo), COL-05 (R-D), ORQ-01 (R-B), ORQ-03 (R-C), ORQ-04 (R-A), CHW-02 (R-G, silêncio permanente) e CHW-12 (critério estrutural).
- Contagem: 40 de 51 PASS, 11 PARCIAL, 0 sem teste. A classificação de PASS usa o sensor: as mutações dos requisitos reclassificados foram mortas (M02, M15 a M17, M26, M33, M36, M37, M42, M43, M49, M52 a M54, M73, M82, M84 a M88, M96, M97, M116, M118, M127, M131 a M134, M143 a M147), exceto as listadas na tabela de sobreviventes.

### Resumo da iteração 2

**Overall**: ❌ Not Ready (iteração 2 de 3)
**Sensor**: 142 de 169 mortos; 19 sobreviventes reais.
**Gate**: 206 passed, 0 failed.
**Próximo passo**: corrigir R-D, R-A, R-C, R-B; trocar os 5 testes vazios ou fracos por testes que discriminem; cobrir N100, N98c, N109, N110, N113; tratar B13; reexecutar (iteração 3).

---

---

# Iteração 1 (histórico)

## Veredito da iteração 1: FAIL ❌ (histórico)

Motivos, em ordem de peso:
1. O sensor de discriminação deixou 49 de 129 mutantes sobreviverem (40 são lacunas reais de teste, 9 são equivalentes ou redundantes por defesa em profundidade).
2. Foram reproduzidos 17 defeitos de lógica por execução em cópia isolada. Seis são Major: troca de domínio na mesma conversa, fundamentação numérica furada, reaprovação automática de `stale`, perda silenciosa de indexação, usuário sem resposta quando o orquestrador lança exceção, e limite de tentativas de transferência não aplicado.
3. As tarefas T15 a T18 estão abertas (Agent Bot real, verificação manual do console, avaliação ponta a ponta em `docs/AVALIACAO.md`, que não existe, e implantação). Os critérios de sucesso do spec sobre fidelidade medida e latência não estão demonstrados.
4. Nenhum AC está totalmente sem teste, mas 25 de 51 têm teste parcial (parte essencial do resultado definido no spec não é afirmada).

---

## Task Completion

| Task | Status | Notes |
| ---- | ------ | ----- |
| T1 a T14 | ✅ Done | Marcadas feitas em `tasks.md`; código presente. Suíte offline verde (ver Gate). |
| T15 Preparação do Chatwoot | ⚠️ Partial | "Agent Bot criado e testado com conversa real" aberto; `tasks.md` declara `Tests: none`, mas existe `test_chatwoot_client_and_setup.py` (não rastreado). |
| T16 Console web | ⚠️ Partial | Verificação manual no navegador aberta. O frontend não foi exercitado por este verificador. |
| T17 Avaliação | ⚠️ Partial | Execução ponta a ponta com a MariTalk e `docs/AVALIACAO.md` não existem. `tests/eval/test_gates.py` (corpus) NÃO foi executado por restrição. |
| T18 Implantação | ❌ Not done | Aberta. |

---

## Spec-Anchored Acceptance Criteria

Legenda: PASS = a asserção bate com o resultado do spec e nenhum mutante essencial sobreviveu; PARCIAL = existe teste, mas parte essencial do resultado do spec não é afirmada, sobrevive mutante essencial ou há defeito de implementação; GAP = sem teste.
Os caminhos abaixo são relativos a `backend/`. IDs `Mxx` remetem à seção do sensor.

### P1: Ingestão e curadoria

| Critério | Resultado definido no spec | `file:line` + asserção | Resultado |
| -------- | -------------------------- | ---------------------- | --------- |
| ING-01 SHA-256 e páginas, sem escrever nos PDFs | sha256 e nº de páginas por arquivo; PDF intacto | `tests/integration/test_ingestion_pipeline.py:25` `inv[0]["sha256"] == hashlib.sha256(b"mesmo").hexdigest()`; `:34` `pdf.read_bytes() == before` | ⚠️ PARCIAL: `pages` (contagem) nunca é afirmado; `page_counter=lambda p: 5` é um dublê |
| ING-02 texto por página, nº da página, id do documento PJe | doc id e página lidos do rodapé | `tests/unit/test_pje_parser.py:9` `(f.doc_id, f.pje_page, f.signer, f.signed_at) == ("28338236", 3, "DAVI IVA", "2026-05-12T10:14:55")` | ✅ PASS |
| ING-03 OCR só se corpo < 30 caracteres, com confiança | OCR apenas nessa página; confiança gravada | `test_ingestion_pipeline.py:72` `ocr.calls == [3] and rep.pages_ocr == 1`; `:74` `(row["method"], row["ocr_conf"], row["status"]) == ("ocr", 91.0, "ok")` | ⚠️ PARCIAL: o limiar 30 não é discriminado (página de 0 caracteres); M73 (limiar 5) sobreviveu |
| ING-04 confiança < 60 vai a `needs_review` e sai do índice | status `needs_review`, fora da busca | `test_ingestion_pipeline.py:83` `rep.pages_needs_review == 1`; `:84` `search_lexical("ilegível borrada", ...) == []` | ✅ PASS (M75 e M77 mortos) |
| ING-05 remove rodapé e une hifenização sem alterar números | "protocolada"; `R$ 14.166,48` e data intactos | `tests/unit/test_text_and_privacy.py:7` `"protocolada" in t and "R$ 14.166,48" in t and "12/05/2026" in t`; `tests/unit/test_pje_parser.py:26` | ✅ PASS |
| ING-06 trechos ≤ 900 com metadados | ≤ 900 caracteres; processo, documento, tipo, data, página, arquivo, hash, data de indexação | `test_text_and_privacy.py:13` `all(len(c) <= 900 for c in chunks)`; `test_ingestion_pipeline.py:37-38` (metadados no documento) | ⚠️ PARCIAL: o teste passa 900 explícito; M88 (`MAX_CHUNK_CHARS` 900 para 2000, o padrão usado pela ingestão) sobreviveu; hash do trecho não é afirmado |
| ING-07 metadado ausente vira `desconhecido` | `"desconhecido"` | `tests/unit/test_pje_parser.py:41` `cover.process_class == "desconhecido" and cover.secrecy == "desconhecido" and cover.rows == []` | ✅ PASS |
| ING-08 duplicata por hash no mesmo processo | um indexado, outro registrado como duplicata | `test_ingestion_pipeline.py:91` `rep.chunks_duplicate == 1` | ✅ PASS (M99 morto); ver B12 (duplicata contra trecho de documento pendente) |
| ING-09 pula PDF com mesmo SHA; reprocessa só o processo alterado | `skipped_unchanged`; documentos substituídos sem duplicar | `test_ingestion_pipeline.py:61` `status == "skipped_unchanged"`; `:64` `len(store.list_documents(limit=100)) == 5` | ⚠️ PARCIAL: só há um processo no teste; M145 (apagar documentos de todos os processos) sobreviveu; ver B4 |
| ING-10 relatório (páginas, OCR, needs_review, duplicatas, erros, duração) | JSON com esses campos | `test_ingestion_pipeline.py:123` `summary["totals"]["errors"] == 1 and summary["totals"]["documents"] == 5`; `:124` `Path(summary["report_path"]).exists()` | ⚠️ PARCIAL: totais de OCR, needs_review, duplicatas e duração do relatório agregado não são afirmados |
| CUR-01 só `approved` é consultável | pendentes fora da busca | `test_ingestion_pipeline.py:46` `hits == []`; `:109` `store.indexed_chunk_count() == 0` | ✅ PASS (M01 morto) |
| CUR-02 sigilo SIM ou tipo sensível mantém `pending_review` | `PENDING_REVIEW` | `tests/unit/test_policies_and_state.py:24` `triage(**kw).review_state is ReviewState.PENDING_REVIEW` (7 casos) | ✅ PASS (M19 a M23 mortos) |
| CUR-03 revisor, data, motivo e atualização do índice | decisão registrada; índice atualizado | `tests/integration/test_api.py:77` `r.json()["chunks_indexed"] == 1`; `test_ingestion_pipeline.py:103` `"Revisora" in d.review_reason`; `test_retrieval.py:58` | ⚠️ PARCIAL: validação de revisor e motivo obrigatórios no serviço (M82) e aprovação tornando `public` (M134) sobreviveram; a data da decisão não é afirmada |
| SEC-01 máscara de CPF, RG, telefone, e-mail, CEP e contagem por documento | texto sem os dados; contagens | `test_text_and_privacy.py:23-24` `n["cpf"] == 1 and n["email"] == 1 and n["telefone"] == 1 and n["cep"] == 1 and n["rg"] == 1`; `test_ingestion_pipeline.py:53-54` | ⚠️ PARCIAL: CPF, CEP e telefone sem rótulo não são discriminados (M15, M16, M17 sobreviveram, porque os testes usam sempre "CPF", "CEP", "tel" antes); título e `ctx` não são mascarados (B8); contagem por documento não é afirmada |

### P1: Hybrid RAG

| Critério | Resultado definido no spec | `file:line` + asserção | Resultado |
| -------- | -------------------------- | ---------------------- | --------- |
| RAG-01 BM25 + vetor + RRF k = 60 | fusão por RRF com k = 60 | `tests/integration/test_retrieval.py:29` `abs(fused[1] - 1 / 61) < 1e-9`; `:36` `res.evidences[0].lexical_rank and ...vector_rank` | ⚠️ PARCIAL: M27 (k padrão do retriever 60 para 10), M118 (fusão sem a lista vetorial) e M119 (sem a lexical) sobreviveram; a asserção `:28` é quase vacuosa (`A and B and C or D`, em que D vale sozinho) |
| RAG-02 isolamento por domínio | trecho de um domínio nunca aparece em outro | `test_retrieval.py:44` `all(e.domain is D.PROCESSUAL for e in proc.evidences)`; `:45` `[e.doc_id for e in inst.evidences] == ["i1"]`; `test_orchestrator.py:57` | ✅ PASS (cada camada de filtro isolada sobrevive sozinha, M59 e M63, mas as duas juntas são mortas, C01) |
| RAG-03 filtro `approved` + `public` antes da fusão e do contexto | pendente, rejeitado e restrito nunca recuperados | `test_retrieval.py:51` `not {"x_pend","x_rej","x_restr"} & {e.doc_id for e in res.evidences}` | ⚠️ PARCIAL: M02 (remover `access_class = 'public'` do `_ELIGIBLE`) sobreviveu, pois o doc restrito do teste é criado com `index=False`; "antes da fusão" não é observável pelo teste (spec-precision) |
| RAG-04 abstenção por limiar ou processo ausente | abster, informar a limitação, oferecer encaminhamento | `test_retrieval.py:64` `abstain_reason == "processo_ausente_do_acervo"`; `:107` `abstain_reason == "baixa_similaridade_semantica"`; `test_orchestrator.py:69` `"encaminhamento" in t.reply.text and t.state.offer_pending` | ✅ PASS (M24, M25, M90 mortos; M26 sobreviveu, ver sensor) |
| RAG-05 referências com processo, documento, tipo, data, página, arquivo | todos os seis campos (processual); título, órgão, data, URL (demais) | `test_orchestrator.py:48` `"Fontes:" in ... and f"processo {P1}" in ... and "página 2" in ...` | ⚠️ PARCIAL: documento, tipo, data e arquivo não são afirmados (M84, M85, M86, M87 sobreviveram); o formato de domínios B e C não é afirmado |
| RAG-06 referência existe e números, datas e valores têm lastro | reprova fato sem lastro | `tests/unit/test_intent_and_grounding.py:56-58` `"data_sem_lastro:2026-08-01" in ...problems`, valor e processo | ⚠️ PARCIAL: números isolados (M12) sobrevivem; a implementação deixa passar valor por substring, prazos e datas por extenso (B2) |
| RAG-07 falha de fundamentação: descartar e abster com encaminhamento | `fundamentacao_nao_verificada`, sem a data inventada | `test_orchestrator.py:101` `abstain_reason == "fundamentacao_nao_verificada"`; `:102` `"15/12/2026" not in t.reply.text` | ✅ PASS (M66 morto) |
| SEC-02 instrução em trecho é dado; registrar o evento | remove do contexto e registra | `test_orchestrator.py:127` `"Ignore todas as instruções" not in user and "[trecho removido" in user`; `:128` `t.reply.trace["injection_flagged"]` | ⚠️ PARCIAL: o evento só vai para `trace`; nunca para `audit` nem para log (B14); registro persistente não é afirmado |
| RAG-08 aviso de acervo de demonstração | texto do aviso na resposta processual | `test_orchestrator.py:49` `"não é consulta em tempo real ao PJe" in t.reply.text` | ✅ PASS (M65 morto) |
| RAG-09 reranker reordena antes de selecionar o contexto | ordem alterada, `reranked` verdadeiro | `test_retrieval.py:88` `rer.stages.get("reranked") is True`; `:89` ordem diferente ou `len(base.evidences) == 1` | ⚠️ PARCIAL: M143 (reranquear depois do corte `top_k`) sobreviveu, e a cláusula `or len(...) == 1` enfraquece a asserção |

### P1: Orquestrador e Chatwoot (núcleo)

| Critério | Resultado definido no spec | `file:line` + asserção | Resultado |
| -------- | -------------------------- | ---------------------- | --------- |
| ORQ-01 seis intenções e domínio | classe correta por mensagem | `tests/unit/test_intent_and_grounding.py:21` `classify_intent(msg).intent is expected` (7 casos, as 6 classes) | ✅ PASS (falso positivo de `atendimento_humano`, ver B7) |
| ORQ-02 perfil muda a linguagem, não as regras | system prompt e evidências idênticos | `test_orchestrator.py:184` `sys_a == sys_b`; `:185` evidências iguais | ✅ PASS (M133 morto) |
| ORQ-03 esclarecer se há mais de um processo candidato | `CLARIFY` listando os dois processos | `test_orchestrator.py:95` `t.reply.kind is K.CLARIFY and P1 in t.reply.text and P2 in t.reply.text` | ✅ PASS (M69, M70 mortos) |
| ORQ-04 pedir encaminhamento (pedido, evidência insuficiente, fontes divergem, falha 2 vezes) | `HANDOFF` | `test_orchestrator.py:89`; `:84`; `:174` `...reply.kind is K.HANDOFF` | ⚠️ PARCIAL: o teste de aceite (`:72-77`) passa por acidente: com `if st.offer_pending ...` desligado (M92), "sim" vira abstenção e o 2º insucesso dispara handoff do mesmo jeito; "fontes divergem" não tem gatilho de runtime nem teste; `answer.handoff` do modelo (M71) sem teste |
| CHW-01 estados e transições definidas | transições inválidas levantam erro | `tests/unit/test_policies_and_state.py:34-37` `ensure_transition(...) is S.HANDOFF_REQUESTED`; `pytest.raises(InvalidTransition)` (2 casos) | ✅ PASS (M06 e M123 mortos; só 3 transições verificadas) |
| CHW-02 sem resposta automática em handoff_requested, handoff_in_progress, human_active | silêncio | `test_policies_and_state.py:42` `{s for s in S if bot_may_reply(s)} == {BOT_ACTIVE, AUTOMATION_RESUMED}`; `test_chatwoot_handler.py:83-84` `outcome == "silent"` e `len(gw.sent) == n` | ✅ PASS (M04, M05, M48 mortos) |
| CHW-03 transferência não confirmada: manter `handoff_requested`, não anunciar, repetir até o limite | sem "Encaminhei"; estado `HANDOFF_REQUESTED` | `test_chatwoot_handler.py:91-93`; `:100-101` | ⚠️ PARCIAL: o teste só injeta exceção; `assign_team` devolvendo `False` (equipe inexistente) não é testado (M36, M37 sobreviveram); o limite configurado não é testado (M43) nem aplicado (B6) |
| CHW-04 retomada por comando explícito, com quem e quando | `automation_resumed`, auditado | `test_chatwoot_handler.py:112` `st.handoff_state is S.AUTOMATION_RESUMED`; `:115` `"transition:human_closed->automation_resumed" in audit` | ⚠️ PARCIAL: ator e motivo não são afirmados (M49 sobreviveu) |
| CHW-05 evento duplicado processado uma vez, por conta, conversa e id | `duplicate`, 1 envio | `test_chatwoot_handler.py:55` `process(...) == "duplicate"`; `:56` `len(gw.sent) == 1`; `test_api.py:48` | ⚠️ PARCIAL: M96 (chave sem conversa) e M97 (sem conta) sobreviveram; não há teste "mesmo id em outra conversa não é duplicata" |
| CHW-06 erro registrado sem dado sensível, nova tentativa com recuo exponencial, mensagem não perdida | 3 chamadas; evento liberado | `tests/integration/test_chatwoot_client_and_setup.py:93` `len([c ... "/messages"]) == 3`; `test_chatwoot_handler.py:147` `process(h, payload).outcome == "processed"` | ⚠️ PARCIAL: o recuo exponencial nunca é medido (`backoff_s=0`; M146 sobreviveu); "sem dados sensíveis" não é testado |
| CHW-07 ignora bot, agente humano e privadas | `ignored` | `test_chatwoot_handler.py:62-64` | ✅ PASS (M44 morto; M45 é redundante, ver sensor) |

### P2: Coletores

| Critério | Resultado definido no spec | `file:line` + asserção | Resultado |
| -------- | -------------------------- | ---------------------- | --------- |
| COL-01 fora da lista: recusar e registrar | exceção e log `recusada` | `tests/integration/test_collection.py:53` `svc.fetcher.calls == [] and [...] == ["recusada"] * 3` | ✅ PASS (M29, M30, M31 mortos) |
| COL-02 procedência, hash, vigência, `pending_review` | URL, data, órgão, hash, publicação, estado | `test_collection.py:60` `d.review_state is R.PENDING_REVIEW and d.collected_at and d.content_hash and d.published_at == "2021-05-18"` | ⚠️ PARCIAL: `valid_from` e `valid_until` nunca são preenchidos pelo coletor (só `validity_flag`); ver sensor M52 |
| COL-03 nada consultável sem aprovação | busca vazia até aprovar | `test_collection.py:66` `not search(...).sufficient`; `:74` `res.sufficient and res.evidences[0].citation["url"] == BV` | ✅ PASS (M56 morto) |
| COL-04 mesmo hash vira duplicado | "marcá-los como duplicados" | `test_collection.py:85` `d.review_state is R.REJECTED and a["doc_id"] in d.review_reason` | ✅ PASS com ⚠️ spec-precision: o spec não define o estado "duplicado"; a implementação usa `rejected` |
| COL-05 coleta velha vira `stale` e sai das respostas até nova revisão | `STALE`, fora da busca | `test_collection.py:101` `refresh_staleness(now=future) == [r["doc_id"]]`; `:102` `... is R.STALE` | ⚠️ PARCIAL: a "nova revisão" é contornável (B3); não existe agendamento, só o comando manual `maintenance`; M52 sobreviveu |
| COL-06 divergência entre fontes volta para revisão | `NEEDS_REVIEW` nos dois | `test_collection.py:116` `len(pairs) == 1`; `:118` `... is R.NEEDS_REVIEW` | ⚠️ PARCIAL: só caso positivo; M53 (mesma fonte) e M54 (mesmo conteúdo) sobreviveram |
| COL-07 respeita `robots.txt`, sem credenciais | bloqueia Disallow; suspende se inacessível | `test_collection.py:151` `pytest.raises(CollectionError, match="robots")`; `:158` `match="robots.txt"` | ⚠️ PARCIAL: robots com 5xx (M33) não testado; o redirecionamento contata o host não autorizado antes de recusar (B10) |
| COL-08 revogado ou rejeitado sai das buscas | busca vazia | `test_collection.py:76`; `test_retrieval.py:58` `"d2" not in {...}` | ✅ PASS |
| COL-09 texto original por artigo, sem resumo, com referência normativa | `summary is None`, `Art. 2º` | `test_collection.py:124` `d.extra["summary"] is None and d.extra["original_text_preserved"]`; `:126` `any(c.endswith("Art. 2º") for c in ctxs)` | ✅ PASS (M138 morto) |

### P2: Integração Chatwoot

| Critério | Resultado definido no spec | `file:line` + asserção | Resultado |
| -------- | -------------------------- | ---------------------- | --------- |
| CHW-08 cria só o que falta, sem diferenciar caixa | segunda execução não cria nada | `test_chatwoot_client_and_setup.py:116` `again["created_teams"] == [] and again["created_labels"] == []`; `:117` `len(fake.teams) == 5 and len(fake.labels) == 6` | ✅ PASS |
| CHW-09 responde na conversa de origem com `ia_orquestrador`, `ia_rag` e a de intenção | mensagem e 3 etiquetas | `test_chatwoot_handler.py:47` `"20/07/2026" in gw.sent[0][1]`; `:48` `{"ia_orquestrador","ia_rag","consulta_processual"} <= set(gw.labels[77])` | ⚠️ PARCIAL: a conversa de destino da mensagem não é afirmada (M144 sobreviveu); intenção jurídica recebe a etiqueta `duvida_institucional` (`orchestrator.py:48`; M131 sobreviveu) |
| CHW-10 atribuir equipe, `open`, etiqueta `humano`, só então informar | ordem | `test_chatwoot_handler.py:72` `gw.assigned == [(77,"Atendimento Humano Geral")] and gw.statuses == [(77,"open")]`; `:73` `"humano" in gw.labels[77]` | ⚠️ PARCIAL: a ordem entre a etiqueta e o aviso não é testada (M42 sobreviveu); aviso antes da atribuição é morto (M41) |
| CHW-11 falha de processamento aplica `ia_falha` | etiqueta | `test_chatwoot_handler.py:93`; `:146`; `:160` `"ia_falha" in gw.labels[77]` | ✅ PASS (M129 morto) |
| CHW-12 inbox, equipe, etiqueta e estado em campos distintos | separação | indireto: `test_chatwoot_handler.py:72-74` (equipe no gateway, etiqueta no gateway, estado em `ops`) | ⚠️ PARCIAL: critério estrutural, sem teste direto; inbox não é modelada |

### P3: Console

| Critério | Resultado definido no spec | `file:line` + asserção | Resultado |
| -------- | -------------------------- | ---------------------- | --------- |
| UI-01 listar por estado e aprovar ou rejeitar | lista de pendentes; aprovação indexa | `tests/integration/test_api.py:75` `[d["doc_id"] for d in pend] == ["p1"]`; `:77` `r.status_code == 200 and r.json()["chunks_indexed"] == 1` | ✅ PASS no nível da API; o frontend (`frontend/app.js`) não tem teste e a verificação manual (T16) está aberta |
| UI-02 sem token: HTTP 401 | 401 | `test_api.py:67` `client.get(path).status_code == 401` (3 rotas) | ✅ PASS (M80 morto) |

**Contagem**: 51 ACs; 26 PASS; 25 PARCIAL; 0 sem nenhum teste. Itens de AC sem teste algum: ver "Elementos de AC sem teste" abaixo.

**Status**: ❌ Lacunas presentes. ⚠️ Há spec-precision gaps (lista abaixo).

### Elementos de AC sem teste

ING-01 contagem de páginas; ING-10 totais do relatório agregado; CHW-03 limite de tentativas e `assign_team` devolvendo `False`; CHW-04 ator e motivo; CHW-05 chave por conta e conversa; CHW-06 recuo exponencial e ausência de dado sensível; CHW-10 ordem etiqueta e aviso; COL-06 casos negativos; COL-07 robots com 5xx; RAG-05 documento, tipo, data e arquivo; ORQ-04 "fontes divergem" e `encaminhar` do modelo; SEC-01 formas sem rótulo, título e `ctx`; CUR-03 revisor e motivo obrigatórios.

### Imprecisões da spec (spec-precision gaps)

1. COL-04 "marcá-los como duplicados": não há estado "duplicado"; a implementação rejeita o segundo registro (`collection/service.py:107`).
2. ORQ-04 "evidência insuficiente... solicitar o encaminhamento": o código oferece o encaminhamento na 1ª vez e só encaminha na 2ª; "fontes divergem" não tem gatilho de runtime (COL-06 só retira da consulta).
3. RAG-03 "antes da fusão": ordem não observável por teste de caixa-preta.
4. CHW-03 "até o limite configurado": o spec não diz o que acontece depois do limite (parar? escalar? silenciar?).
5. CHW-09 "etiqueta de intenção correspondente": só existem 6 etiquetas (`chatwoot_setup`); `duvida_juridica` não existe.
6. COL-05 "prazo de validade configurado": não define o gatilho (agendamento) nem a relação com `valid_until`; `stale_after_days` existe em `Settings` e em `sources.yaml`.
7. SEC-02 "registrar o evento": não diz onde (log, auditoria, trace).
8. CHW-02 lido literalmente conflita com CHW-03: em `handoff_requested` o bot envia o aviso de nova tentativa.
9. RAG-06 "números": não define faixa; o código só confere números isolados com 5 ou mais dígitos.
10. COL-02 "datas de vigência quando existirem": o coletor não as grava.
11. ING-06 "guardar em cada trecho": os metadados ficam no documento (join), não no trecho.
12. CHW-12 não é testável como escrito.

---

## Discrimination Sensor

Cópia isolada de `backend/` (sem `.venv`) em diretório de rascunho fora do repositório; uma falha por vez; `pytest -q -x` na cópia; árvore real intocada. Scripts: `mutate.py`, `mutate2.py` e `probes/` no diretório de rascunho da sessão (a cópia foi apagada ao final).

**Sensor depth**: P0 completo (≥ 5 exigidos; 129 mutantes injetados à mão em 25 arquivos, sem ferramenta de mutação automática).

Placar da iteração 1: 80 mortos, 49 sobreviventes de 129 (62%; 67% sem os 9 equivalentes).

### Sobreviventes que são lacunas de teste (40), cada um vira tarefa de correção

| Mutante | File:line | Mutação | Requisito |
| ------- | --------- | ------- | --------- |
| M02 | `app/infrastructure/sqlite/knowledge_store.py:71` | `_ELIGIBLE` sem `d.access_class = 'public'` | RAG-03, CUR-02 |
| M12 | `app/application/answering/grounding.py:113` | números isolados sem lastro ignorados | RAG-06 |
| M15 | `app/domain/privacy.py:43` | sem máscara de CPF formatado sem rótulo | SEC-01 |
| M16 | `app/domain/privacy.py:54` | sem máscara de CEP sem rótulo | SEC-01 |
| M17 | `app/domain/privacy.py:49` | sem máscara de telefone com DDD sem rótulo | SEC-01 |
| M26 | `app/application/retrieval/hybrid.py:188` | sem exigência de acerto lexical | RAG-04 |
| M27 | `app/application/retrieval/hybrid.py:105` | `rrf_k` padrão 60 para 10 | RAG-01 |
| M118 | `app/application/retrieval/hybrid.py:145` | RRF só com a lista lexical | RAG-01 |
| M119 | `app/application/retrieval/hybrid.py:145` | RRF só com a lista vetorial | RAG-01 |
| M116 | `app/application/retrieval/hybrid.py:176` | corte `top_k` + 5 | RAG-01 |
| M143 | `app/application/retrieval/hybrid.py:169` | reranker aplicado depois do corte `top_k` | RAG-09 |
| M33 | `app/infrastructure/web/fetcher.py:48` | robots com 5xx não suspende a coleta | COL-07 |
| M36 | `app/application/chat/handler.py:168` | `confirmed = True` (ignora retorno da API) | CHW-03 |
| M37 | `app/application/chat/handler.py:168` | confirma mesmo com `assign_team` falso | CHW-03, CHW-10 |
| M42 | `app/application/chat/handler.py:166` | aviso ao usuário antes da etiqueta `humano` | CHW-10 |
| M43 | `app/application/chat/handler.py:180` | limite de tentativas ignorado | CHW-03 |
| M49 | `app/application/chat/handler.py:208` | retomada sem registrar ator e motivo | CHW-04 |
| M96 | `app/application/chat/handler.py:65` | chave de idempotência sem a conversa | CHW-05 |
| M97 | `app/application/chat/handler.py:65` | chave de idempotência sem a conta | CHW-05 |
| M144 | `app/application/chat/handler.py:142` | resposta enviada à conversa errada | CHW-09 |
| M127 | `app/application/chat/handler.py:134` | `QuestionError` não tratado no canal | (borda) |
| M146 | `app/infrastructure/chatwoot/client.py:49` | recuo exponencial vira constante | CHW-06 |
| M52 | `app/application/collection/service.py:147` | `stale` ignora `valid_until` | COL-05 |
| M53 | `app/application/collection/service.py:163` | divergência também para a mesma fonte | COL-06 |
| M54 | `app/application/collection/service.py:163` | divergência mesmo com conteúdo igual | COL-06 |
| M134 | `app/infrastructure/sqlite/knowledge_store.py:349` | aprovar não torna o documento `public` | CUR-03 |
| M145 | `app/infrastructure/sqlite/knowledge_store.py:206` | reingestão apaga documentos de todos os processos | ING-09 |
| M147 | `app/infrastructure/sqlite/knowledge_store.py:329` | `mark_duplicates` ignora o processo | ING-08 |
| M71 | `app/application/answering/orchestrator.py:224` | ignora `encaminhar` do modelo | ORQ-04 |
| M92 | `app/application/answering/orchestrator.py:111` | aceite do encaminhamento desligado (teste passa pelo 2º insucesso) | ORQ-04 |
| M112 | `app/application/answering/orchestrator.py:184` | segunda tentativa (reformulação) ignorada | RAG-04 |
| M131 | `app/application/answering/orchestrator.py:48` | etiqueta da intenção jurídica | CHW-09 |
| M132 | `app/application/answering/orchestrator.py:39` | equipe do domínio jurídico trocada | CHW-10 |
| M73 | `app/application/ingestion/service.py:183` | limiar de OCR 30 para 5 | ING-03 |
| M82 | `app/application/curation/service.py:20` | revisor e motivo opcionais | CUR-03 |
| M84 a M87 | `app/application/answering/prompts.py:95,96,94,91` | citação sem documento PJe, arquivo, data, tipo e título | RAG-05 |
| M88 | `app/application/ingestion/text_processing.py:11` | `MAX_CHUNK_CHARS` 900 para 2000 | ING-06 |

Contagem: 40 lacunas reais (M84 a M87 contadas individualmente).

### Sobreviventes equivalentes ou redundantes por defesa em profundidade (9)

M03 (`knowledge_store.py:71`, sem `active = 1`; `active` só vai a 0 junto com estados não aprovados), M45 (`handler.py:106`, o ramo seguinte já ignora o bot), M59 (`knowledge_store.py:509`) e M63 (`:530`) (filtros de domínio redundantes entre si; removidos juntos, C01, o teste os mata), M64 (`:358`, `_drop_index` redundante com `_ELIGIBLE`), M94 (`api/main.py:26`, o orquestrador também limita a 1000), M98b e M101 (`knowledge_store.py:270,203`, resíduos de FTS e vetores órfãos são inofensivos pelos joins), M135 (`:351`, `active` redundante).

### Mortos (80)

M01, M04 a M11, M13, M14, M18 a M25, M29 a M32, M34, M35, M38 a M41, M44, M46 a M48, M50, M51, M55 a M58, M60 a M62, M65 a M70, M72, M74 a M81, M83, M89, M90, M93, M99, C01, M113, M123 a M126, M128 a M130, M133, M136 a M138, M140 a M142, M148, M149 (trechos de `knowledge_store.py:71`, `handoff.py:26`, `operational_store.py:102`, `grounding.py:81-106`, `privacy.py:45,48`, `policies.py:79-89`, `hybrid.py:186,190`, `sources.py:67-71`, `fetcher.py:50,56,67`, `handler.py:83,90,100,118,127,165-167,201`, `collection/service.py:95,106,147,165`, `orchestrator.py:34,107,167,171,236`, `ingestion/service.py:138,206,258,268,280`, `api/main.py:58,73`). Observação: M08 e M47 foram mortos com saída de log de erro (a suíte falhou), não por asserção do fluxo.

---

## Revisão adversarial de lógica

Todos reproduzidos por teste descartável na cópia isolada (`probes/test_probe.py`, `probes/test_probe2.py`), exceto onde marcado "(por leitura)". Ordem de gravidade.

**B1 [Major] Troca de domínio na mesma conversa abstém.** `orchestrator.py:180,183` passa `st.process_number` a qualquer domínio; `knowledge_store.py:471,510` filtra por `d.process_number`. Cenário: sessão com "Quando é a audiência de conciliação do processo 1234567-89.2026.8.03.0001?" (resposta ok), depois "Qual o horário do Balcão Virtual?" na mesma sessão devolve `ABSTAIN` com `abstain_reason = sem_resultados` (`retrieval_lexical_hits: 0`), e a pergunta institucional seguinte dispara handoff automático (2º insucesso, `orchestrator.py:262`). Correção: só repassar `process_number` quando o domínio é processual, ou zerar ao mudar de domínio.

**B2 [Major] Fundamentação numérica furada (RAG-06).** `grounding.py:294` compara os valores por substring dos dígitos; `:303-306` só confere números com 5 ou mais dígitos; datas por extenso não são checadas. Cenários, todos com `ok = True`: evidência "R$ 11.000,00" e resposta "R$ 1.000,00"; evidência "5 dias" e resposta "15 dias"; evidência "15 dias" e resposta "30 dias"; evidência "20/07/2026" e resposta "21 de julho de 2026". Só "R$ 5.000,01" contra "R$ 5.000,00" é reprovado.

**B3 [Major] Reaprovação automática de `stale` (COL-05).** `collection/service.py:100-104`: `lookup_decision` devolve a última decisão humana (aprovação), que nunca é invalidada por `mark_review_system`. Cenário: coletar, aprovar, `refresh_staleness(now+400d)` marca `STALE`; nova coleta com o mesmo conteúdo devolve `state: approved` sem revisor, e a página volta às respostas, o que contraria "até nova revisão".

**B4 [Major] Perda silenciosa de indexação (ING-09).** `ingestion/service.py:156-167`: `replace_process` grava o SHA antes de `index_pending`. Cenário: o embedder lança erro no meio, o relatório sai com `status = error`; a execução seguinte devolve `skipped_unchanged` (`:138`) e `indexed_chunk_count() == 0` permanece. Só `--force` recupera.

**B5 [Major] Exceção no orquestrador deixa o usuário sem resposta (CHW-11, borda).** `chat/handler.py:132-137` só captura `QuestionError`; `orchestrator.py:144-149,169-175,277` (`latest_document`, `evidences_for_doc`, `_clarify`, `_list_processes`) estão fora do `try`. Cenário: `store.latest_document` lança `RuntimeError("db locked")` com "Qual a última decisão do processo ...?"; `handle` devolve `error`, aplica `ia_falha` e `gw.sent == []`: o cidadão não recebe nada.

**B6 [Major] Limite de tentativas de transferência não existe (CHW-03).** `handler.py:180` só escolhe o texto; `_retry_handoff` repete sempre. Cenário: `assign_team` falha sempre e `max_handoff_attempts = 3`: após 8 mensagens `handoff_attempts = 8`, estado `handoff_requested`, 8 tentativas e cada mensagem recebe `HANDOFF_FAILED_TEXT`, que afirma "A equipe foi sinalizada e retomará a conversa", o que não é verdade (só há a etiqueta `ia_falha`).

**B7 [Medium] Falso positivo de transferência.** `intent.py:17-21`, regex `transfer(ir|encia)` e `atendente` soltos. Cenário: "A decisão do processo 1234567-89.2026.8.03.0001 determinou transferência de valores?" devolve `HANDOFF` ("pedido do usuário") em vez de consulta processual. Por leitura: "não quero falar com atendente" também é lida como pedido.

**B8 [Medium] Dado pessoal fora da máscara (SEC-01).** `ingestion/service.py:244-247,296` grava `title = seg.name` e `ctx` sem `mask_pii`; `ctx` entra em `chunks_fts` (`knowledge_store.py:314`) e `titulo` aparece na citação (`prompts.py:177`). Cenário: nome do documento na tabela da capa "Petição enviada por ana.silva@exemplo.com" (tipo comum, aprovado): `title` e `ctx` mantêm o e-mail, e `search_lexical("ana silva exemplo")` o encontra. O portão `tests/eval/test_gates.py:63` só inspeciona `chunks.text`.

**B9 [Medium] Aviso de transferência pode não ser entregue.** `handler.py:172-178`: o `send_message(HANDOFF_OK_TEXT)` fica fora de `try`. Cenário: Chatwoot confirma equipe e status, mas o envio falha: estado `human_active`, resultado `error`, evento liberado; a reentrega cai em `silent` (CHW-02) e o usuário nunca é avisado.

**B10 [Medium] Redirecionamento contata host não autorizado (COL-01, COL-07).** `fetcher.py:269` usa `follow_redirects=True` e só compara o host em `:307`, depois do GET. Cenário: `balcao.php` responde 302 para `https://malicioso.example/x`: a lista de hosts contatados é `[centralservicos..., centralservicos..., malicioso.example]`; a exceção vem depois. `robots.txt` do destino não é consultado.

**B11 [Medium] Trechos curtos descartados.** `text_processing.py:289` remove trechos com menos de 20 caracteres alfanuméricos: `chunk_text("Defiro.")`, `("Cite-se a ré.")` e `("Indefiro o pedido.")` devolvem `[]`. Um despacho curto fica `approved` com zero trechos e a pergunta responde "não consta". Impacto no acervo real não medido (corpus proibido a este verificador).

**B12 [Low] Duplicata contra documento pendente.** `knowledge_store.py:324-339` marca duplicatas entre documentos de qualquer estado. Cenário: o mesmo parágrafo no documento 1000003 (`pending_review`) e no 1000004 (`approved`): o trecho do aprovado fica `duplicate_of = 3, indexed = 0` e nunca entra no índice.

**B13 [Low-Medium, risco sem Chatwoot real] Retomada pode ser ineficaz.** `handler.py:117-120`: após `resume_automation`, se `conversation.meta.assignee` continua preenchido (o Chatwoot mantém o responsável após resolver), a próxima mensagem leva a `human_active` e o bot permanece em silêncio (sonda: `outcome silent`, 0 respostas). `resume_automation` não desatribui.

**B14 [Low, configuração] Eco de mensagem do bot com token de usuário.** `container.py:69-77` aceita só `api_token`; `client.py:32` usa esse token para enviar. O eco chega com `sender.type = "user"` e `handler.py:108-111` assume agente humano: após a 1ª resposta o estado vira `human_active` (sonda confirmada).

**B15 [Low, risco] Evento de status sem `updated_at`.** `handler.py:67`: a chave usa `updated_at or ''`; um segundo `resolved` da mesma conversa vira `duplicate` e o estado fica `human_active` para sempre (sonda confirmada com payload sem `updated_at`).

**B16 [Low, por leitura] Metadados de documento pendente expostos pela capa aprovada.** `ingestion/service.py:292-293,332-335`: só documentos `RESTRICTED` são ocultados na cronologia da capa; pendentes por marcador de sigilo no texto ou sem texto legível aparecem com tipo, nome e data.

**B17 [Spec/implementação] Desvios sem teste:** SEC-02 não registra o evento (sonda: `audit` só tem `reply:answer`); COL-05 depende de comando manual (`cli.py:115-119`); COL-02 vigência nunca preenchida; `AbstentionPolicy.min_lexical_hits` (`policies.py:99`) e `_NUMBER` (`grounding.py:207`) são código morto.

**Defesas verificadas e que seguram:** `_ELIGIBLE` reaplicado em lexical, vetor e `load_evidences`; capa pendente não vaza (`evidences_for_doc` passa por `load_evidences`); idempotência atômica (`INSERT` com `IntegrityError` sob `RLock`); `bot_may_reply` consultado antes de qualquer resposta; transferência não confirmada não é anunciada; robots inacessível suspende; redirect para outro host é recusado (embora contatado, B10).

**Testes que espelham a implementação ou são fracos:** `test_orchestrator.py:72-77` (passa sem o aceite, M92); `test_retrieval.py:28` (precedência `and/or` torna a asserção quase vacuosa); `test_retrieval.py:89` (`or len(base.evidences) == 1`); `test_chatwoot_handler.py:73` (não afirma ordem); `FakeGateway.assign_team` nunca devolve `False`, de modo que o ramo real `team_id is None` (`client.py:75`) nunca é exercitado.

---

## Interactive UAT Results

Não realizado: o verificador não tem navegador nem Chatwoot, e foi proibido de usar rede, LLM e índice real.

---

## Code Quality

| Principle | Status |
| --------- | ------ |
| Minimum code | ⚠️ código morto (`min_lexical_hits`, `_NUMBER`) |
| Surgical changes | ✅ (mudanças restritas ao novo projeto) |
| No scope creep | ✅ |
| Matches patterns | ✅ camadas domain, application, infrastructure, interfaces respeitadas |
| Spec-anchored outcome check (valores afirmados batem com o spec) | ❌ 25 de 51 ACs parciais |
| Per-layer Coverage Expectation (domínio 1:1 com ACs; rotas feliz + borda + erro) | ❌ ramos de erro do canal (B5, B9) e de `assign_team` falso sem teste |
| Every test maps to a spec requirement | ⚠️ `test_health`, `test_console_*` mapeiam a bordas do spec; `test_gates.py` (corpus) não foi executado |
| Guidelines seguidas | `backend/pytest.ini` e `tasks.md` (Test Coverage Matrix); nenhum `AGENTS.md` ou `CONTRIBUTING.md` |

---

## Edge Cases (spec.md)

- [x] PDF corrompido: erro no relatório e segue (`test_ingestion_pipeline.py:123`).
- [x] Pergunta vazia ou > 1000 caracteres: 422 (`test_api.py:55-56`); no canal Chatwoot o ramo `QuestionError` não tem teste (M127).
- [x] Falha do LLM: abstenção (`test_orchestrator.py:112`).
- [ ] "Documento mais recente": só funciona quando a pergunta cita o tipo (`orchestrator.py:187-190`); sem tipo, cai na recuperação comum (a sonda "último documento" abstém, "última audiência" abstém; não afirma data errada, mas também não ordena por data). Ordem por data do documento testada em `test_orchestrator.py:134`.
- [x] Processo ausente do acervo: `test_orchestrator.py:63-64`.
- [x] Texto pedindo para ignorar regras: `test_orchestrator.py:162`.

---

## Gate Check

- **Gate command**: `cd backend && .venv/bin/python -m pytest -q` (nível Full). O gate Build (`-m corpus tests/eval` e `evals.run_eval --mode retrieval`) NÃO foi executado: exige o índice real, proibido a este verificador.
- **Result**: 135 passed, 0 failed, 6 deselected (os 6 do marcador `corpus` em `tests/eval/test_gates.py`), 1 warning (depreciação do anyio) em ~1 s.
- **Test count before feature**: não comparável (o commit 9dba3b7 substituiu o chatbot comercial e a suíte anterior era de outro sistema).
- **Test count after feature**: 135 offline + 6 corpus.
- **Skipped tests**: 6 `corpus` por desenho (`pytest.ini: addopts = -m "not live and not corpus"`); não executados aqui.
- **Failures**: nenhuma na suíte original. Na cópia isolada, 129 mutantes e 20+ sondas.

---

## Fix Plans

Tarefas de correção propostas (prioridade):

1. **Blocker/Major**: B1 (filtrar `process_number` por domínio, `orchestrator.py:180,183`), B2 (verificar valores por token, números de qualquer tamanho e datas por extenso, `grounding.py:292-306`), B3 (`stale` exige nova decisão humana: invalidar a decisão anterior em `mark_review_system` ou ignorar `lookup_decision` para `STALE`), B4 (gravar SHA só depois de indexar), B5 (capturar exceção no orquestrador e responder ao usuário), B6 (aplicar `max_handoff_attempts` e corrigir o texto).
2. **Major (testes)**: adicionar os 40 testes para os sobreviventes acima; prioridade para M36, M37, M43, M96, M97 (Chatwoot), M12, M27, M118, M119 (RAG), M15 a M17 (PII sem rótulo), M02 (acesso `public`), M92 (aceite do encaminhamento), M84 a M87 (citação completa).
3. **Medium**: B7, B8, B9, B10, B11.
4. **Low e riscos**: B12 a B16; testar B13 e B15 com payload real do Chatwoot 4.11.1.
5. **Processo**: commitar `test_chatwoot_client_and_setup.py` e `test_gates.py`; concluir T15 a T18; rodar o gate Build com o índice real e registrar em `docs/AVALIACAO.md`.

---

## Requirement Traceability Update

Sugestão (o verificador não altera `spec.md`):

| Requirement | Previous Status | New Status |
| ----------- | --------------- | ---------- |
| ING-02, ING-04, ING-05, ING-07, ING-08, CUR-01, CUR-02, RAG-02, RAG-04, RAG-07, RAG-08, ORQ-01, ORQ-02, ORQ-03, CHW-01, CHW-02, CHW-07, COL-01, COL-03, COL-04, COL-08, COL-09, CHW-08, CHW-11, UI-01, UI-02 | Pending | ✅ Verified (UI-01 só no nível da API) |
| ING-01, ING-03, ING-06, ING-09, ING-10, CUR-03, SEC-01, RAG-01, RAG-03, RAG-05, RAG-06, RAG-09, SEC-02, ORQ-04, CHW-03, CHW-04, CHW-05, CHW-06, COL-02, COL-05, COL-06, COL-07, CHW-09, CHW-10, CHW-12 | Pending | ❌ Needs Fix (teste e/ou implementação) |

---

## Summary

**Overall**: ❌ Not Ready

**Spec-anchored check**: 26 de 51 ACs batem com o resultado do spec; 25 parciais; 12 spec-precision gaps.
**Sensor**: 80 de 129 mutantes mortos; 49 sobreviventes (40 lacunas reais).
**Gate**: 135 passed, 0 failed (Build com índice real não executado).

**What works**: filtro de elegibilidade em três camadas, máquina de estados, bloqueio do bot após a transferência, idempotência atômica, isolamento por domínio, abstenção por processo ausente, recusa de hosts fora da lista, máscara de PII rotulado, curadoria protegida por token.

**Issues found**: B1 a B6 (Major), B7 a B11 (Medium), B12 a B17 (Low e riscos) e 40 mutantes sobreviventes.

**Next steps**: executar o plano de correção acima e re-verificar (iteração 1 de no máximo 3).
