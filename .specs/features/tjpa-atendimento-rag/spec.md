# Atendimento Omnichannel com Hybrid RAG (piloto TJPA) Specification

## Problem Statement

O piloto do hackathon do TJPA precisa atender cidadãos e advogados pelo WhatsApp (via Chatwoot já instalado) com respostas verificáveis sobre três domínios: Informações Processuais (PDFs públicos de demonstração), Conhecimento Institucional e Conhecimento Jurídico-Informacional (ambos coletados de fontes oficiais autorizadas). Não existe API oficial de consulta processual, não existe base institucional consolidada, e a base atual do repositório (chatbot comercial da Morpheus IA com MariTalk e SQLite) não tem RAG, Agno nem Chatwoot. O sistema deve responder só com evidência, citar a origem, abster-se quando a evidência for insuficiente e encaminhar para atendimento humano, sem nunca afirmar que consulta o PJe em tempo real.

## Goals

- [ ] Ingerir os 10 PDFs de `docs/processos` sem modificá-los, preservando página, documento PJe e proveniência, com OCR somente nas páginas sem texto no corpo.
- [ ] Responder perguntas dos três domínios via Hybrid RAG (BM25 + vetorial + RRF + filtros + reranking opcional) com citação verificável e abstenção por critérios objetivos.
- [ ] Coletar conteúdo institucional e jurídico apenas de fontes em lista de permissão, mantendo-o em `pending_review` até aprovação humana.
- [ ] Integrar com o Chatwoot (recebimento, resposta, etiquetas, equipes, transferência) com estado de atendimento explícito, idempotência e bloqueio do bot após a transferência.
- [ ] Publicar um relatório de avaliação com recall, fidelidade, rastreabilidade, abstenção, latência e taxa de erro, mais critérios de promoção.

## Out of Scope

| Feature | Reason |
| ------- | ------ |
| Integração com PJe, e-SAJ ou qualquer API oficial de consulta processual | Não existe API no piloto; fica atrás de uma porta (`ProcessLookupPort`) sem implementação simulada. |
| Atendimento por telefone e presencial | Sem mecanismo de registro no Chatwoot; não pode ser declarado integrado. |
| Canal de e-mail | Sem implementação funcional no piloto; documentado como evolução. |
| Graph RAG | Só se os resultados de avaliação justificarem a complexidade. |
| Declaração de conformidade LGPD/CNJ | Depende de avaliação dos responsáveis institucionais. |
| Treinar ou ajustar modelos | O piloto usa modelo hospedado (MariTalk) sem fine-tuning. |

---

## Assumptions & Open Questions

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --- | --- | --- | --- |
| Os 10 PDFs são do Tribunal de Justiça do Amapá (TJAP), não do TJPA | Tratados como acervo de demonstração; o sistema não os atribui ao TJPA | A capa dos PDFs diz "Tribunal de Justiça do Estado do Amapá", os números usam J.TR 8.03 e as URLs são `pje.tjap.jus.br` | n (precisa do aval do usuário) |
| Autorização de uso do acervo | Declaração do usuário de que são processos abertos e públicos, registrada como base de autorização `declared_by_owner`, sem confirmação institucional | O prompt afirma que são públicos; a política exige registrar a origem da autorização e não presumi-la | n (declaração do usuário) |
| Classificação de acesso automática | Documento é `approved` só se a capa diz "Segredo de justiça? NÃO", o tipo documental não está na lista sensível e não há marcador de sigilo no texto; os demais ficam `pending_review` fora do índice consultável | Evita publicar automaticamente documento que exija validação | n (agent default) |
| Dados pessoais no índice | CPF, RG, telefone, e-mail e CEP são mascarados no texto indexado; nomes de partes e advogados permanecem | Minimiza dado pessoal sem inviabilizar a busca por partes; o PDF original não é alterado | n (agent default) |
| Modelo de embeddings | `paraphrase-multilingual-MiniLM-L12-v2` via fastembed (ONNX, 384 dimensões), configurável | Cabe na VM de 3,9 GB que já hospeda Chatwoot e dashboard; qualidade medida na avaliação | n (agent default, revisável por métrica) |
| Armazenamento | SQLite com FTS5 (BM25) e vetores em BLOB com busca por força bruta em NumPy, atrás de portas | Corpus abaixo de 100 mil trechos; reprodutível, sem serviço extra; substituível por pgvector | n (agent default) |
| Orquestração | Fluxo determinístico na camada de aplicação; Agno `Agent` gera a resposta com saída estruturada, sem ferramentas de recuperação autônomas | Acesso, filtros e abstenção ficam verificáveis em código e testáveis com LLM falso | n (agent default) |
| LLM | MariTalk (`sabiazinho-4`) pelo Agno `OpenAILike`, como já configurado no repositório | Preserva o provedor existente; testes usam LLM falso | y (já em uso no repositório) |
| Reranking | Porta com implementação nula por padrão e cross-encoder opcional (`jina-reranker-v2-base-multilingual`) | O prompt diz "quando disponível"; o modelo pesa 1,1 GB e não cabe na VM | n (agent default) |
| Mecanismo de bot no Chatwoot | Agent Bot com webhook; o estado de atendimento fica em tabela própria, e etiqueta não controla a execução | O prompt proíbe etiqueta como único controle | n (agent default) |
| Equipes e etiquetas do Chatwoot | As 5 equipes já existem (nomes em minúsculas); as etiquetas ainda não existem e serão criadas por script idempotente | Inspeção do banco da VM em 2026-10-09 | y (verificado) |
| Implantação | Contêiner do backend na VM `srv1633081`, ao lado do Chatwoot e do dashboard, que não são alterados | Autorização do usuário em 2026-10-09 | y (usuário) |
| Retomada da automação | Só por comando explícito `resume_automation` (agente humano ou API autenticada), com motivo registrado | O prompt exige condição operacional explícita e verificável | n (agent default) |

**Open questions:** none - all resolved or logged above (required before the spec is confirmed).

---

## User Stories

### P1: Ingestão e curadoria do corpus processual ⭐ MVP

**User Story**: Como equipe do piloto, quero inventariar, extrair, avaliar e curar os PDFs de `docs/processos` para que só trechos autorizados e rastreáveis cheguem ao índice.

**Why P1**: Sem corpus validado não há Informações Processuais.

**Acceptance Criteria**:

1. The system SHALL calcular SHA-256 e contar páginas de cada PDF de `docs/processos` sem escrever nesses arquivos.  <!-- ING-01 -->
2. WHEN um PDF é processado THEN the system SHALL extrair o texto por página preservando o número da página de origem e o identificador do documento PJe lido do rodapé.  <!-- ING-02 -->
3. WHEN o corpo de uma página, descontado o rodapé do PJe, tem menos de 30 caracteres THEN the system SHALL aplicar OCR em português somente a essa página e registrar a confiança do OCR.  <!-- ING-03 -->
4. IF a confiança média do OCR de uma página é menor que 60 THEN the system SHALL marcar a página como `needs_review` e excluí-la do índice aprovado.  <!-- ING-04 -->
5. WHEN o texto é normalizado THEN the system SHALL remover o rodapé repetido e unir hifenizações de fim de linha sem alterar números, datas ou valores.  <!-- ING-05 -->
6. The system SHALL dividir o texto em trechos de no máximo 900 caracteres, sem cruzar documentos PJe, e guardar em cada trecho processo, documento, tipo, data, página, arquivo, hash e data de indexação.  <!-- ING-06 -->
7. WHEN um metadado não pode ser lido do PDF THEN the system SHALL gravá-lo como `desconhecido` em vez de inferi-lo.  <!-- ING-07 -->
8. WHEN dois trechos têm o mesmo hash de conteúdo normalizado dentro do mesmo processo THEN the system SHALL indexar um deles e registrar o outro como duplicata.  <!-- ING-08 -->
9. WHEN a ingestão roda de novo sobre um PDF cujo SHA-256 não mudou THEN the system SHALL pular o PDF, e WHEN o hash mudou THEN the system SHALL reprocessar e substituir apenas os trechos desse processo.  <!-- ING-09 -->
10. WHEN a ingestão termina THEN the system SHALL gravar um relatório com páginas por arquivo, páginas com OCR, páginas `needs_review`, duplicatas, erros e duração.  <!-- ING-10 -->
11. The system SHALL manter cada documento com estado `pending_review`, `approved` ou `rejected` e indexar para consulta apenas os `approved`.  <!-- CUR-01 -->
12. WHEN a capa indica "Segredo de justiça? SIM" ou o tipo documental está na lista sensível THEN the system SHALL manter o documento em `pending_review`.  <!-- CUR-02 -->
13. WHEN um revisor aprova ou rejeita um documento THEN the system SHALL registrar revisor, data e motivo e atualizar o índice.  <!-- CUR-03 -->
14. The system SHALL mascarar CPF, RG, telefone, e-mail e CEP no texto indexado e gravar a contagem de ocorrências por documento.  <!-- SEC-01 -->

**Independent Test**: Rodar `python -m app.interfaces.cli ingest` duas vezes e conferir o relatório, o pulo na segunda execução e a ausência de trechos de documentos `pending_review` na busca.

---

### P1: Hybrid RAG com abstenção e citação ⭐ MVP

**User Story**: Como cidadão ou advogado, quero respostas fundamentadas com origem citada, ou a indicação clara de que o sistema não sabe.

**Why P1**: É a razão de ser do piloto.

**Acceptance Criteria**:

1. WHEN uma pergunta chega THEN the system SHALL executar busca lexical BM25 e busca vetorial e combinar os resultados por Reciprocal Rank Fusion com k = 60.  <!-- RAG-01 -->
2. The system SHALL restringir cada consulta ao domínio selecionado, de modo que um trecho de um domínio nunca apareça em consulta de outro.  <!-- RAG-02 -->
3. The system SHALL aplicar o filtro de estado `approved` e de classe de acesso `public` antes da fusão e antes de montar o contexto de geração.  <!-- RAG-03 -->
4. IF nenhum trecho recuperado atinge o limiar mínimo de relevância, ou a pergunta cita um número de processo ausente do acervo THEN the system SHALL abster-se, informar a limitação e oferecer encaminhamento.  <!-- RAG-04 -->
5. The system SHALL anexar a cada resposta as referências usadas com processo, documento, tipo, data, página e arquivo (processual) ou título, órgão, data e URL (demais domínios).  <!-- RAG-05 -->
6. WHEN o modelo devolve uma resposta THEN the system SHALL verificar que toda referência citada existe no conjunto recuperado e que números, datas e valores da resposta aparecem nos trechos citados.  <!-- RAG-06 -->
7. IF a verificação de fundamentação falha THEN the system SHALL descartar a resposta do modelo e responder com abstenção e encaminhamento.  <!-- RAG-07 -->
8. IF um trecho recuperado contém instruções dirigidas ao assistente THEN the system SHALL tratá-lo como dado, sem alterar regras de segurança, e registrar o evento.  <!-- SEC-02 -->
9. The system SHALL declarar em respostas processuais que se baseiam em documentos do acervo de demonstração e não em consulta em tempo real ao PJe.  <!-- RAG-08 -->
10. WHERE um reranker está configurado the system SHALL reordenar os candidatos fundidos antes de selecionar o contexto.  <!-- RAG-09 -->

**Independent Test**: Executar o conjunto de avaliação com LLM falso e conferir recall@k, abstenção e citações.

---

### P1: Orquestrador, perfil e estado de atendimento ⭐ MVP

**User Story**: Como atendente, quero que o bot encaminhe a conversa quando necessário e pare de responder depois, até autorização explícita.

**Why P1**: Exigência de segurança e de operação do atendimento humano.

**Acceptance Criteria**:

1. WHEN uma mensagem chega THEN the system SHALL classificar a intenção em `consulta_processual`, `duvida_institucional`, `duvida_juridica`, `atendimento_humano`, `saudacao` ou `fora_de_escopo` e escolher o domínio correspondente.  <!-- ORQ-01 -->
2. The system SHALL adaptar a linguagem ao perfil `cidadao`, `advogado` ou `indefinido` sem alterar regras de segurança, fundamentação ou acesso.  <!-- ORQ-02 -->
3. IF a pergunta processual não traz número de processo e há mais de um candidato no acervo THEN the system SHALL pedir esclarecimento antes de responder.  <!-- ORQ-03 -->
4. WHEN o usuário pede atendimento humano, a evidência é insuficiente, as fontes divergem ou a recuperação falha duas vezes THEN the system SHALL solicitar o encaminhamento.  <!-- ORQ-04 -->
5. The system SHALL manter o estado de atendimento entre `bot_active`, `handoff_requested`, `handoff_in_progress`, `human_active`, `human_closed` e `automation_resumed`, aceitando apenas as transições definidas.  <!-- CHW-01 -->
6. WHILE o estado é `handoff_requested`, `handoff_in_progress` ou `human_active` the system SHALL não gerar resposta automática.  <!-- CHW-02 -->
7. IF o Chatwoot não confirma a transferência THEN the system SHALL manter `handoff_requested`, não informar ao usuário que a transferência foi concluída e repetir a tentativa até o limite configurado.  <!-- CHW-03 -->
8. WHEN o estado é `human_closed` e chega o comando explícito de retomada THEN the system SHALL passar a `automation_resumed` e registrar quem autorizou e quando.  <!-- CHW-04 -->
9. WHEN o mesmo evento do Chatwoot chega mais de uma vez THEN the system SHALL processá-lo uma única vez, identificado por conta, conversa e id da mensagem.  <!-- CHW-05 -->
10. IF uma chamada ao Chatwoot falha THEN the system SHALL registrar o erro sem dados sensíveis, aplicar nova tentativa com recuo exponencial e não perder a mensagem recebida.  <!-- CHW-06 -->
11. The system SHALL ignorar mensagens enviadas pelo próprio bot ou por agentes humanos e mensagens privadas.  <!-- CHW-07 -->

**Independent Test**: Simular eventos do Chatwoot com um cliente falso e conferir estados, respostas e duplicidade.

---

### P2: Coletores institucional e jurídico com curadoria

**User Story**: Como curador, quero coletar conteúdo de fontes oficiais autorizadas e aprová-lo antes de ele chegar ao índice.

**Why P2**: Alimenta os domínios B e C, mas o piloto processual funciona sem eles.

**Acceptance Criteria**:

1. IF a URL está fora da lista configurável de fontes autorizadas THEN the system SHALL recusar a coleta e registrar a recusa.  <!-- COL-01 -->
2. WHEN uma página é coletada THEN the system SHALL registrar URL original, data de coleta, órgão responsável, hash do conteúdo, datas de publicação ou vigência quando existirem, e estado `pending_review`.  <!-- COL-02 -->
3. The system SHALL não indexar para consulta nenhum registro coletado sem aprovação de um revisor.  <!-- COL-03 -->
4. WHEN dois registros têm o mesmo hash de conteúdo THEN the system SHALL marcá-los como duplicados.  <!-- COL-04 -->
5. WHEN a data de coleta de um registro aprovado ultrapassa o prazo de validade configurado THEN the system SHALL marcá-lo `stale` e retirá-lo das respostas até nova revisão.  <!-- COL-05 -->
6. WHEN dois registros aprovados de fontes diferentes tratam do mesmo tema com conteúdo distinto THEN the system SHALL sinalizar a divergência e encaminhar o tema para revisão.  <!-- COL-06 -->
7. The system SHALL respeitar o `robots.txt` e nunca enviar credenciais nem contornar autenticação.  <!-- COL-07 -->
8. WHEN um registro é revogado ou rejeitado THEN the system SHALL desativá-lo e removê-lo das buscas.  <!-- COL-08 -->
9. WHERE o conteúdo é jurídico the system SHALL guardar o texto original da norma separado de qualquer resumo gerado e registrar a referência normativa.  <!-- COL-09 -->

**Independent Test**: Coletar uma página fixture, aprová-la e verificar que só então aparece na busca do domínio.

---

### P2: Integração Chatwoot, inboxes, equipes e etiquetas

**User Story**: Como operador, quero o bot ligado ao Chatwoot, com equipes, etiquetas e transferência corretas.

**Why P2**: Fecha o fluxo WhatsApp ponta a ponta.

**Acceptance Criteria**:

1. WHEN o script de configuração roda THEN the system SHALL criar apenas as etiquetas e equipes ausentes, comparando nomes sem diferenciar maiúsculas.  <!-- CHW-08 -->
2. WHEN o bot responde THEN the system SHALL enviar a mensagem pela API do Chatwoot na conversa de origem e aplicar as etiquetas `ia_orquestrador`, `ia_rag` e a de intenção correspondente.  <!-- CHW-09 -->
3. WHEN a transferência é solicitada THEN the system SHALL atribuir a conversa à equipe do domínio da intenção, mudar o status para `open`, aplicar a etiqueta `humano` e só então informar o usuário.  <!-- CHW-10 -->
4. IF o processamento de uma mensagem falha THEN the system SHALL aplicar a etiqueta `ia_falha`.  <!-- CHW-11 -->
5. The system SHALL separar inbox (canal), equipe (responsável), etiqueta (classificação) e estado de atendimento em campos distintos.  <!-- CHW-12 -->

**Independent Test**: Rodar o script em modo `--dry-run` e depois real contra o Chatwoot da VM, repetindo para confirmar que não duplica.

---

### P3: Console web de teste e curadoria

**User Story**: Como curador, quero uma página para conversar com o assistente e aprovar ou rejeitar documentos.

**Why P3**: Dá observabilidade na demonstração, mas CLI e API cobrem a função.

**Acceptance Criteria**:

1. WHEN o curador abre o console THEN the system SHALL listar documentos e registros por estado com a opção de aprovar ou rejeitar.  <!-- UI-01 -->
2. IF o token administrativo não é informado THEN the system SHALL negar as rotas de curadoria com HTTP 401.  <!-- UI-02 -->

**Independent Test**: Abrir o console, aprovar um documento e conferir a mudança de estado.

---

## Edge Cases

- IF o PDF não abre ou está corrompido THEN the system SHALL registrar o erro no relatório e seguir com os demais arquivos.
- IF a pergunta é vazia ou excede 1000 caracteres THEN the system SHALL rejeitá-la com HTTP 422.
- IF o provedor do LLM falha THEN the system SHALL responder com abstenção e solicitar encaminhamento.
- WHEN a pergunta pede "o documento mais recente" THEN the system SHALL ordenar pela data do documento e declarar o critério, nunca pela data de indexação.
- IF a pergunta pede dado de processo ausente do acervo THEN the system SHALL informar que o acervo de demonstração não o contém.
- IF o texto de entrada pede para ignorar regras do sistema THEN the system SHALL manter as regras e tratar o texto como pergunta comum.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| ING-01 | P1: Ingestão | Design | Pending |
| ING-02 | P1: Ingestão | Design | Pending |
| ING-03 | P1: Ingestão | Design | Pending |
| ING-04 | P1: Ingestão | Design | Pending |
| ING-05 | P1: Ingestão | Design | Pending |
| ING-06 | P1: Ingestão | Design | Pending |
| ING-07 | P1: Ingestão | Design | Pending |
| ING-08 | P1: Ingestão | Design | Pending |
| ING-09 | P1: Ingestão | Design | Pending |
| ING-10 | P1: Ingestão | Design | Pending |
| CUR-01 | P1: Ingestão | Design | Pending |
| CUR-02 | P1: Ingestão | Design | Pending |
| CUR-03 | P1: Ingestão | Design | Pending |
| SEC-01 | P1: Ingestão | Design | Pending |
| RAG-01 | P1: Hybrid RAG | Design | Pending |
| RAG-02 | P1: Hybrid RAG | Design | Pending |
| RAG-03 | P1: Hybrid RAG | Design | Pending |
| RAG-04 | P1: Hybrid RAG | Design | Pending |
| RAG-05 | P1: Hybrid RAG | Design | Pending |
| RAG-06 | P1: Hybrid RAG | Design | Pending |
| RAG-07 | P1: Hybrid RAG | Design | Pending |
| RAG-08 | P1: Hybrid RAG | Design | Pending |
| RAG-09 | P1: Hybrid RAG | Design | Pending |
| SEC-02 | P1: Hybrid RAG | Design | Pending |
| ORQ-01 | P1: Orquestrador | Design | Pending |
| ORQ-02 | P1: Orquestrador | Design | Pending |
| ORQ-03 | P1: Orquestrador | Design | Pending |
| ORQ-04 | P1: Orquestrador | Design | Pending |
| CHW-01 | P1: Orquestrador | Design | Pending |
| CHW-02 | P1: Orquestrador | Design | Pending |
| CHW-03 | P1: Orquestrador | Design | Pending |
| CHW-04 | P1: Orquestrador | Design | Pending |
| CHW-05 | P1: Orquestrador | Design | Pending |
| CHW-06 | P1: Orquestrador | Design | Pending |
| CHW-07 | P1: Orquestrador | Design | Pending |
| COL-01 | P2: Coletores | Design | Pending |
| COL-02 | P2: Coletores | Design | Pending |
| COL-03 | P2: Coletores | Design | Pending |
| COL-04 | P2: Coletores | Design | Pending |
| COL-05 | P2: Coletores | Design | Pending |
| COL-06 | P2: Coletores | Design | Pending |
| COL-07 | P2: Coletores | Design | Pending |
| COL-08 | P2: Coletores | Design | Pending |
| COL-09 | P2: Coletores | Design | Pending |
| CHW-08 | P2: Chatwoot | Design | Pending |
| CHW-09 | P2: Chatwoot | Design | Pending |
| CHW-10 | P2: Chatwoot | Design | Pending |
| CHW-11 | P2: Chatwoot | Design | Pending |
| CHW-12 | P2: Chatwoot | Design | Pending |
| UI-01 | P3: Console | - | Pending |
| UI-02 | P3: Console | - | Pending |

**Coverage:** 51 total, 0 mapped to tasks, 51 unmapped ⚠️

---

## Success Criteria

- [ ] Os 10 PDFs são ingeridos, com relatório sem erros não explicados e sem alteração nos arquivos originais (SHA-256 igual antes e depois).
- [ ] Nenhum trecho `pending_review`, `rejected` ou `stale` aparece em resultado de busca (teste automatizado).
- [ ] A avaliação reporta recall@5, fidelidade, taxa de abstenção correta e latência medidos, não estimados.
- [ ] Depois da transferência confirmada, nenhuma resposta automática é enviada até a retomada explícita (teste automatizado).
