# Diagnóstico inicial (Fases 1 e 2)

Data da inspeção: 2026-10-09. Cada item recebe um rótulo: **CONFIRMADO** (visto no repositório, na VM ou nos PDFs), **INFERIDO** (dedução razoável, ainda sem prova) ou **DESCONHECIDO**.

## 1. Repositório

| Item | Estado | Evidência |
| --- | --- | --- |
| O repositório continha o site e o chatbot comerciais da Morpheus IA (FastAPI, SQLModel/SQLite, MariTalk pelo SDK `openai`, captura de leads) | CONFIRMADO | `backend/app/{api,llm,storage}`, `.specs/features/initial-support-chatbot` (removidos no commit `9dba3b7`) |
| Não havia Chatwoot, Agno, RAG, embeddings, BM25, coleta ou OCR no código | CONFIRMADO | busca por `agno`, `chatwoot`, `embedding` e `bm25` sem resultados |
| Testes existentes cobriam só o chatbot comercial (9 arquivos) | CONFIRMADO | `backend/tests`, removidos junto com o projeto |
| O provedor LLM em uso era a MariTalk (`sabiazinho-4`), com chave no `.env` | CONFIRMADO | `.env` (chave não registrada aqui) |
| Agno instalável no ambiente | CONFIRMADO | `agno 3.1.2` instalado em `backend/.venv` |
| Tecnologia de armazenamento vetorial pré-existente | CONFIRMADO: nenhuma | Só havia SQLite para leads |
| A integração com a MariTalk pelo Agno funciona com a chave atual | CONFIRMADO | resposta real obtida em 2026-10-09 (`cli ask`) |

Decisão: o projeto anterior foi removido (autorizado pelo usuário) e o novo backend nasceu em camadas (`domain`, `application`, `infrastructure`, `interfaces`). Reaproveitamos o provedor MariTalk, o SQLite e o pytest. Não havia componente funcional a preservar para o novo escopo.

## 2. Ambiente e VM

| Item | Estado | Evidência |
| --- | --- | --- |
| Chatwoot v4.11.1 na VM `srv1633081`, com a inbox WhatsApp Cloud "WhatsApp TJPA" (id 1) | CONFIRMADO | `inboxes` no banco e API |
| Já existiam as 5 equipes pedidas (nomes em minúsculas, como o Chatwoot grava) | CONFIRMADO | `GET /teams` |
| Não existia nenhuma etiqueta nem Agent Bot | CONFIRMADO | `GET /labels`, `GET /agent_bots` (antes de 2026-10-09) |
| Dashboard e Chatwoot compartilham a VM e não devem ser removidos | CONFIRMADO | orientação do usuário |
| VM: 1 vCPU, 3,9 GB de RAM (cerca de 2,1 GB livres), disco com 34 GB livres | CONFIRMADO | `free -m`, `df -h` |
| O nginx descartava o cabeçalho `api_access_token` (sublinhado) no vhost do Chatwoot | CONFIRMADO | HTTP 401 com token válido; resolvido com `underscores_in_headers on` (backup em `/root/chatwoot.nginx.bak-*`) |
| Poppler, Tesseract (`por`) e Python 3.12 disponíveis na máquina de desenvolvimento | CONFIRMADO | `pdftotext`, `tesseract --list-langs` |

## 3. Corpus `docs/processos`

| Item | Estado | Evidência |
| --- | --- | --- |
| 10 PDFs, 518 MB, 7.893 páginas, todos exportações do PJe (iText 5.5.13), nenhum criptografado | CONFIRMADO | `pdfinfo` |
| Nenhuma duplicata exata entre os arquivos | CONFIRMADO | SHA-256 distintos |
| Todos têm camada de texto, mas **362 páginas (4,6%) são imagem** e só trazem o rodapé como texto; precisam de OCR | CONFIRMADO | corpo menor que 30 caracteres depois de remover o rodapé |
| O rodapé do PJe ("Assinado eletronicamente por … Num. N - Pág. P") dá o id do documento e a página dentro dele | CONFIRMADO | 99,6% das páginas (7.858 de 7.893) |
| A capa traz classe, órgão, valor, assuntos, partes, sigilo e a tabela de documentos (id, data, nome, tipo) | CONFIRMADO | parser aplicado aos 10 arquivos |
| O processo `6070124-68.2025.8.03.0001` tem 5.869 páginas e contém PDFs de outro tribunal (TRF1) aninhados, com rodapés duplos | CONFIRMADO | rodapés `pje1g.trf1.jus.br` dentro de `pje.tjap.jus.br` |
| **Os PDFs são do Tribunal de Justiça do Amapá (TJAP), não do TJPA**: capa "Tribunal de Justiça do Estado do Amapá", código `8.03` no número CNJ, URLs `pje.tjap.jus.br` | CONFIRMADO | capas e rodapés |
| Todas as capas dizem "Segredo de justiça? NÃO" | CONFIRMADO | parser |
| O acervo contém dados pessoais (CPF, endereço, telefone, e-mail, contracheque, documentos de identidade) | CONFIRMADO | texto extraído; 29 documentos retidos por triagem |
| Os PDFs são "abertos e públicos" e autorizados para o piloto | DESCONHECIDO | só a declaração do usuário no pedido; sem confirmação institucional |
| Datas de assinatura "Usuário do sistema" de 2025 em processos de 2023 indicam migração, não a data real do ato | INFERIDO | rodapés do processo `0000218-64.2023…` |
| Um processo pode ter documentos fora da tabela da capa | CONFIRMADO (1 caso) | `notInCover = 1` no maior processo; fica com data `desconhecido` |

## 4. Pontos que exigem decisão ou conhecimento externo

1. **TJAP versus TJPA.** O acervo é de outro tribunal. O sistema o trata como acervo de demonstração e nunca o atribui ao TJPA. Se o hackathon exige processos do TJPA, é preciso trocar os PDFs.
2. **Envio de trechos a um serviço externo.** Cada pergunta processual envia até 6 trechos (com dados pessoais já mascarados, mas com nomes de partes) à API da Maritaca AI. O prompt do projeto pede autorização e controles antes de enviar documentos a serviços externos. Tratamos como risco aberto (ver `docs/EVOLUCAO.md`).
3. **Retenção de dados pelo provedor LLM.** DESCONHECIDO; deve ser verificada nos termos da Maritaca AI.
4. **Dados institucionais do TJPA.** Só existem coletas das páginas do Balcão Virtual e de normas; endereços e telefones por unidade dependem da aprovação do curador.
5. **Conformidade LGPD e normas do CNJ.** Não declarada; depende dos responsáveis institucionais.
