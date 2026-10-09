# Operação

Todos os comandos rodam em `backend/` com `.venv/bin/python`. Variáveis em `../.env` (modelo em `.env.example`).

## 1. Variáveis de ambiente

| Variável | Para quê | Padrão |
| --- | --- | --- |
| `MARITALK_API_KEY`, `MARITALK_API_BASE`, `MARITALK_MODEL` | LLM (Agno `OpenAILike`) | modelo `sabiazinho-4` |
| `HF_TOKEN` | download de modelos no Hugging Face | vazio |
| `EMBEDDING_MODEL` | embeddings (fastembed) | `paraphrase-multilingual-MiniLM-L12-v2` |
| `RERANKER_MODEL` | reranker opcional | vazio (desligado) |
| `CORPUS_AUTHORIZED`, `CORPUS_AUTHORIZATION_BASIS` | autorização declarada do acervo processual | `false`, `nao_declarada` |
| `CHATWOOT_BASE_URL`, `CHATWOOT_ACCOUNT_ID` | Chatwoot | URL pública do vhost |
| `CHATWOOT_API_TOKEN` | usuário administrador, só para o script de preparação | vazio |
| `CHATWOOT_BOT_TOKEN` | token do Agent Bot, usado para responder e transferir | vazio |
| `CHATWOOT_WEBHOOK_SECRET` | segredo da URL do webhook | vazio (webhook recusa tudo) |
| `ADMIN_API_TOKEN` | `/api/admin/*` e console | vazio (rotas recusam tudo) |
| `CONSOLE_PUBLIC` | libera o console sem token | `false` |
| `HANDOFF_MAX_ATTEMPTS`, `STALE_AFTER_DAYS`, `RETRIEVAL_*`, `RRF_K`, `MIN_TERM_COVERAGE` | ajustes | ver `app/config.py` |

## 2. Ingestão do acervo processual

```bash
CORPUS_AUTHORIZED=true CORPUS_AUTHORIZATION_BASIS="declarado por <quem> em <data>: <texto>" \
  .venv/bin/python -m app.interfaces.cli ingest            # idempotente: pula PDF com mesmo SHA-256
.venv/bin/python -m app.interfaces.cli ingest --force      # reprocessa (OCR vem do cache)
.venv/bin/python -m app.interfaces.cli ingest --only 6035625
.venv/bin/python -m app.interfaces.cli stats
```

Sem `CORPUS_AUTHORIZED=true` todos os documentos ficam `pending_review` e nada é indexado. O relatório fica em `data/reports/ingestion-*.json`. Os PDFs nunca são alterados.

## 3. Curadoria

```bash
.venv/bin/python -m app.interfaces.cli docs --state pending_review --limit 50
.venv/bin/python -m app.interfaces.cli approve <doc_id> --reviewer "Nome" --reason "motivo"
.venv/bin/python -m app.interfaces.cli reject  <doc_id> --reviewer "Nome" --reason "motivo"
.venv/bin/python -m app.interfaces.cli approve --process 0000000-00.0000.8.03.0000 --reviewer ... --reason ...   # todos os pendentes do processo
```

Também pelo console (`/console`, aba Curadoria) ou por `POST /api/admin/documents/{doc_id}/review`. Aprovar indexa os trechos; rejeitar ou marcar `stale` retira das buscas. Toda decisão fica em `curation_decisions` e é reaplicada se o conteúdo não mudar.

## 4. Coleta institucional e jurídica

```bash
.venv/bin/python -m app.interfaces.cli collect                       # todas as sementes de config/sources.yaml
.venv/bin/python -m app.interfaces.cli collect --domain juridico --url https://www.planalto.gov.br/ccivil_03/leis/l9099.htm
.venv/bin/python -m app.interfaces.cli maintenance                   # marca desatualizados (stale) e divergências
```

Tudo entra como `pending_review` e só passa a ser consultável depois de aprovado. URLs fora da lista são recusadas e registradas em `collection_log`. Para incluir uma fonte nova, edite `backend/config/sources.yaml` (host, domínio, órgão responsável).

## 5. Perguntas e avaliação

```bash
.venv/bin/python -m app.interfaces.cli search "valor da causa do processo ..."          # recuperação sem LLM
.venv/bin/python -m app.interfaces.cli ask "Qual o horário do Balcão Virtual?" --trace   # orquestrador completo
.venv/bin/python -m evals.run_eval --mode retrieval                                       # recall@k, MRR, latência
.venv/bin/python -m evals.run_eval --mode e2e --judge                                     # ponta a ponta com MariTalk
.venv/bin/python -m pytest                                                                # testes offline
.venv/bin/python -m pytest -m corpus                                                      # portões de promoção sobre o índice real
```

## 6. Chatwoot

Estado encontrado em 2026-10-09: inbox `WhatsApp TJPA` (id 1); 5 equipes já existentes; nenhuma etiqueta; nenhum Agent Bot.

```bash
.venv/bin/python -m app.interfaces.chatwoot_setup                       # plano (dry-run)
.venv/bin/python -m app.interfaces.chatwoot_setup --apply               # cria só o que falta (equipes e etiquetas)
.venv/bin/python -m app.interfaces.chatwoot_setup --apply \
  --webhook-url https://srv1633081.hstgr.cloud/atendimento/webhooks/chatwoot --inbox-id 1   # cria o Agent Bot e liga à inbox
```

Ligar o bot à inbox faz as mensagens reais do WhatsApp passarem pelo assistente. Faça depois de validar `/health` e o console. O comando imprime o token do bot uma única vez; guarde em `CHATWOOT_BOT_TOKEN`.

| Inbox | Estado | Observação |
| --- | --- | --- |
| WhatsApp TJPA | integrada | WhatsApp Cloud, id 1 |
| Portal Web, e-mail, telefone, presencial | **não integrados** | sem implementação funcional |

Equipes: Triagem e Orquestração, Informações Processuais, Informações Institucionais, Informações Jurídico-Institucionais, Atendimento Humano Geral (criadas pelo usuário; o Chatwoot as grava em minúsculas).

Etiquetas: `ia_orquestrador`, `ia_rag`, `ia_falha`, `humano`, `consulta_processual`, `duvida_institucional`. O nginx do vhost do Chatwoot precisa de `underscores_in_headers on;` para aceitar o cabeçalho `api_access_token` pela URL pública.

Retomada da automação depois de atendimento humano encerrado:

```bash
curl -X POST https://<host>/atendimento/api/admin/conversations/1/<conversa>/resume \
  -H "Authorization: Bearer $ADMIN_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"actor":"nome.sobrenome","reason":"atendimento concluído"}'
```

## 6.1 Fluxo guiado no WhatsApp, protocolo (TKT) e encerramento

Ativo por padrão (`CHAT_RICH_FLOW=true`; com `false` o bot responde só texto, sem botões nem protocolo).

- **Protocolo:** a primeira mensagem de um atendimento abre um ticket `TKT-XXXXXXXX` (8 hex), com data e hora gravadas em `operational.db` (tabela `tickets`, UTC; exibido no horário de Belém) e anunciado ao cliente. Uma nota privada com o protocolo vai para a equipe. Consulta: `GET /api/admin/tickets` ou a aba "Integração e tickets" do console.
- **Menus:** o Chatwoot envia `input_select` ao WhatsApp Cloud: até 3 opções viram botões; de 4 a 10, lista. Menu principal (lista): 📄 Consultar processo, 🏛️ Balcão Virtual, ⚖️ Termos jurídicos, 🙋 Falar com atendente, ✅ Encerrar atendimento. Depois de escolher um processo: 📋 Dados da capa, 🗓️ Cronologia, 🔎 Outra pergunta. Depois de cada resposta: 📋 Menu principal, 🙋 Atendente, ✅ Encerrar. Texto livre só em "Outra pergunta/dúvida/termo" e quando o usuário simplesmente escreve. O clique chega como o título da opção; o reconhecimento ignora emoji e pontuação.
- **Quem encerra:** o atendimento só termina (1) pelo usuário, tocando em **✅ Encerrar** / **✅ Encerrar atendimento** (digitar a palavra "encerrar", "sair" ou "tchau" **não** encerra: o bot só orienta a usar o botão); (2) pelo atendente, com a nota privada `/encerrar`; (3) quando alguém marca a conversa como Resolvida no Chatwoot; ou (4) por **inatividade**: 23 horas sem nenhuma mensagem (`INACTIVITY_CLOSE_HOURS`, conferido a cada `INACTIVITY_CHECK_MINUTES`=10; 23 h fica dentro da janela de 24 h do WhatsApp, então ainda dá para avisar o cliente). Em todos os casos o ticket é fechado (com quem fechou: usuario, atendente ou sistema), o cliente recebe a mensagem de encerramento com o protocolo e a conversa fica **Resolvida** no Chatwoot. O bot nunca encerra por iniciativa própria fora dessas regras.
- **Novo contato:** depois de um encerramento, a próxima mensagem abre um novo protocolo e devolve a conversa à automação (estado `automation_resumed`, auditado com ator "sistema").

## 6.2 Endpoint para o painel do Chatwoot

O endpoint é `POST /webhooks/chatwoot?token=<segredo>`. Hoje o Agent Bot já o usa pela rede interna. Para cadastrá-lo no painel (Configurações > Integrações > Webhooks), pegue a URL pública pronta em `GET /api/admin/integration` ou na aba "Integração e tickets" do console (eventos: `message_created`, `conversation_resolved`, `conversation_opened`). Webhook e Bot juntos não duplicam respostas, porque cada evento é identificado e processado uma só vez.

## 7. Implantação na VM

A VM `srv1633081` mantém Chatwoot e dashboard; o backend entra como um contêiner a mais (`tjpa_backend`, 700 MB, porta `127.0.0.1:8300`, rede do Chatwoot) e é publicado pelo nginx em `/atendimento/`. A ingestão roda na máquina de desenvolvimento; `data/index/knowledge.db` e `data/models/` seguem para `/opt/tjpa/data`. Os procedimentos usados e o resultado estão em `docs/AVALIACAO.md` (seção Implantação).
