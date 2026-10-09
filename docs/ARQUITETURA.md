# Arquitetura

## 1. Visão geral

```
WhatsApp ──► Chatwoot (inbox) ──► Agent Bot (webhook) ──► Backend FastAPI ──► resposta/transferência ──► Chatwoot
                                                          │
            Console web (frontend/) ───────────────────►  │  Orquestrador (Agno para gerar o texto)
                                                          │      │
                                                          │      ├─ Hybrid RAG: BM25 (FTS5) + vetores + RRF (+ reranker opcional)
                                                          │      ├─ Verificador de fundamentação
                                                          │      └─ Estado de atendimento (SQLite operacional)
                                                          ▼
        knowledge.db (SQLite): documentos, trechos, FTS5, vetores, decisões de curadoria
              ▲                         ▲
   Ingestão de PDFs (CLI)      Coletores B e C (CLI/API) ──► sempre `pending_review` até aprovação humana
```

Camadas (dependências apontam para dentro):

| Camada | Pasta | Conteúdo |
| --- | --- | --- |
| Domínio | `backend/app/domain` | modelos, enums, políticas (triagem, abstenção), máscara de dados pessoais, máquina de estados, portas |
| Aplicação | `backend/app/application` | ingestão, recuperação, orquestração, coletores, curadoria, tratamento de eventos do Chatwoot |
| Infraestrutura | `backend/app/infrastructure` | SQLite, poppler/tesseract, fastembed, Agno/MariTalk, cliente do Chatwoot, busca HTTP |
| Interfaces | `backend/app/interfaces` | API FastAPI, CLI, script de preparação do Chatwoot |
| Frontend | `frontend/` | console estático de conversa e curadoria |

Decisões:

- **Fluxo determinístico, LLM só gera texto.** Domínio, filtros de acesso, suficiência de evidência, esclarecimento e encaminhamento são código testável. O agente Agno recebe só as evidências já filtradas e não tem ferramentas de busca. Assim um trecho de outro domínio ou não aprovado nunca chega ao modelo.
- **SQLite com FTS5 e vetores em BLOB.** O acervo tem cerca de 16 mil trechos indexados; força bruta em NumPy responde em milissegundos e dispensa serviço extra. A porta `KnowledgeStore` permite trocar por pgvector.
- **Embeddings `paraphrase-multilingual-MiniLM-L12-v2` (384 dimensões, fastembed/ONNX).** Cabe na VM de 3,9 GB. O modelo é configurável (`EMBEDDING_MODEL`).
- **Um único agente principal.** Cidadão e advogado são perfis detectados na conversa; mudam só o texto de apoio do prompt, nunca regras, filtros ou fundamentação.

## 2. Domínios e isolamento

| Domínio | Valor | Fonte | Entrada no índice |
| --- | --- | --- | --- |
| A. Informações Processuais | `processual` | `docs/processos` | ingestão de PDFs + triagem automática + curadoria |
| B. Conhecimento Institucional | `institucional` | fontes da lista autorizada (`backend/config/sources.yaml`) | coleta, sempre `pending_review`, depois aprovação humana |
| C. Conhecimento Jurídico-Informacional | `juridico` | idem | idem, com divisão por artigo |

Isolamento: cada consulta tem um domínio único (`chunks.domain`). BM25 e busca vetorial filtram por domínio e por elegibilidade (`review_state = approved`, `access_class = public`, `active = 1`) e `load_evidences` repete o filtro antes de qualquer texto ser usado.

## 3. Modelo de dados (`knowledge.db`)

- `documents`: um documento PJe (domínio A) ou uma página/norma coletada (B e C). Metadados: processo, número, id do documento PJe, tipo, data do documento e sua origem (`tabela_da_capa`), data de assinatura, arquivo, URL, órgão, páginas, classe de acesso, estado de revisão e motivo, hash do conteúdo, datas de indexação e coleta, contagem de dados pessoais mascarados.
- `chunks`: trechos de até 900 caracteres, página do PDF e página dentro do documento, contexto (processo, tipo, nome, data), hash, `duplicate_of`.
- `chunks_fts` (FTS5, `unicode61 remove_diacritics`) e `embeddings` (float32).
- `processes`, `pages`: ficha do processo e qualidade por página (texto ou OCR, confiança, estado).
- `curation_decisions`: toda decisão humana, com revisor, motivo e hash do conteúdo. Se o PDF for reprocessado e o conteúdo não mudar, a decisão é reaplicada.
- `operational.db`: `conversations` (estado de atendimento), `processed_events` (idempotência), `audit`.

Valores ausentes ficam como `desconhecido`; nada é inferido.

## 4. Pipeline de ingestão (Domínio A)

1. Inventário: SHA-256, tamanho, páginas e duplicatas exatas.
2. `pdftotext -layout` do arquivo inteiro, dividido por página.
3. Parser do PJe (`pje_parser.py`): capa, tabela de documentos, rodapé de cada página (id do documento e página). O último rodapé da página vence, porque há PDFs do TRF1 aninhados.
4. Páginas com menos de 30 caracteres de corpo (sem o rodapé) passam por OCR (`pdftoppm` 250 dpi + Tesseract `por`). Confiança média abaixo de 60 marca a página `needs_review` e a exclui do índice. Resultados de OCR ficam em cache por SHA-256 e página.
5. Normalização (une hifenização, compacta espaços, preserva números e datas) e divisão em trechos.
6. Máscara de CPF, RG, telefone, e-mail e CEP. Os nomes de partes e advogados permanecem.
7. Triagem de acesso por documento: só vira `approved` se houver autorização declarada do acervo, a capa disser "Segredo de justiça? NÃO", o tipo e o nome não indicarem documento pessoal e o texto não mencionar sigilo. A capa sintetizada não revela o nome de documentos retidos.
8. Deduplicação de trechos no processo, indexação de FTS e vetores só dos documentos aprovados.
9. Relatório em `data/reports/ingestion-*.json`.

## 5. Pipeline de resposta

1. Validação (1 a 1000 caracteres) e detecção de perfil.
2. Intenção (`consulta_processual`, `duvida_institucional`, `duvida_juridica`, `atendimento_humano`, `saudacao`, `fora_de_escopo`) e domínio. Sem sinais, mas com conversa em andamento, mantém o domínio anterior.
3. Casos determinísticos: lista de processos; "mais recente" pela data do documento na capa; processo citado ausente do acervo.
4. Recuperação: BM25 e vetores (40 candidatos cada), RRF com k = 60, filtros, reranker opcional, boost da ficha da capa quando a pergunta é sobre dados da capa, 6 trechos no contexto. Segunda tentativa com a pergunta reformulada se a primeira for insuficiente.
5. Abstenção por critério: menos de 1 trecho, nenhuma correspondência lexical ou cobertura dos termos da pergunta abaixo de 0,34.
6. Esclarecimento se os melhores trechos vêm de 2 ou mais processos e a pergunta não cita o número.
7. Geração (Agno `Agent`, MariTalk) com o system prompt da ETAPA 6, trechos delimitados como dados e frases com instruções removidas (SEC-02). Saída em JSON.
8. Verificação de fundamentação: referências existentes, datas, valores, números de processo e ids presentes nos trechos citados. Falha descarta a resposta do modelo.
9. Resposta com notas numeradas, lista de fontes montada a partir dos metadados e aviso de que o domínio processual não é consulta em tempo real.
10. Duas abstenções ou falhas seguidas (ou o aceite do usuário) disparam o encaminhamento.

## 6. Chatwoot: conceitos separados

| Conceito | Papel | Implementação |
| --- | --- | --- |
| Inbox | canal de entrada | "WhatsApp TJPA" (id 1, WhatsApp Cloud). Portal Web e e-mail **não** estão integrados |
| Team | responsável pela demanda | escolhida pela intenção: Informações Processuais, Institucionais, Jurídico-Institucionais; pedido do usuário vai para Atendimento Humano Geral; Triagem e Orquestração fica para falhas e triagem manual |
| Etiqueta | classificação operacional | `ia_orquestrador`, `ia_rag`, `ia_falha`, `humano`, `consulta_processual`, `duvida_institucional` |
| Estado de atendimento | controle do bot | tabela `conversations` |

Uma inbox pode enviar a equipes diferentes e uma equipe pode receber de várias inboxes.

Máquina de estados (`domain/handoff.py`):

```
bot_active ─► handoff_requested ─► handoff_in_progress ─► human_active ─► human_closed ─► automation_resumed
                    ▲                        │                                   (somente por comando explícito)
                    └────── falha ───────────┘
```

O bot só responde em `bot_active` e `automation_resumed`. A transferência é confirmada pela API do Chatwoot (atribuição de equipe, status `open` e etiqueta `humano`) e só depois o usuário recebe o aviso. Se o Chatwoot falha, o estado permanece `handoff_requested`, a conversa recebe `ia_falha` e a mensagem seguinte repete a tentativa até o limite. A retomada exige `POST /api/admin/conversations/{conta}/{conversa}/resume` com autor e motivo, registrados na auditoria.

Eventos: o webhook responde 200 de imediato e processa em segundo plano (o Chatwoot desiste em poucos segundos). A chave `msg:{conta}:{conversa}:{mensagem}` garante processamento único; um erro libera a chave para reentrega. Mensagens do bot, privadas e de saída são ignoradas; uma resposta de agente humano ou uma conversa já atribuída a um agente passa a conversa a `human_active`.

## 7. Segurança

- Token administrativo (`ADMIN_API_TOKEN`) em `/api/admin/*` e, por padrão, no console de teste; comparação em tempo constante.
- Segredo do webhook na URL; tokens do Chatwoot e da MariTalk só por variável de ambiente, nunca em log.
- Dados pessoais mascarados antes de indexar; PDFs originais nunca alterados.
- Trechos tratados como dados; instruções dirigidas ao assistente são removidas do contexto e registradas no `trace`.
- Coletor: HTTPS, lista de hosts por domínio, `robots.txt` obrigatório (inacessível suspende a coleta), tamanho máximo, redirecionamento para outro host recusado, sem credenciais.
- Contêiner sem root, publicado só em `127.0.0.1`.
