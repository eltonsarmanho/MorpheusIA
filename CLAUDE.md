# CLAUDE.md

Piloto de atendimento RAG do TJPA (persona Júlia/EscutAI): API FastAPI + Chatwoot + WhatsApp. O acervo de `docs/processos` é do TJAP e serve só de demonstração. Visão geral em `README.md`.

## Comandos

```bash
cd backend
.venv/bin/python -m pytest -q        # suíte offline (sem rede, LLM ou Chatwoot reais); 537 passando
.venv/bin/python -m pytest tests/integration/test_retomada_ticket.py -q   # gate rápido da retomada
```

Ingestão, curadoria, coleta, avaliação, implantação e `chatwoot_setup` estão em `docs/OPERACAO.md`.

## Estrutura

- `backend/app/domain`: regras puras e portas (`ports.py`, `triage_mulher.py`, `units.py`).
- `backend/app/application/chat`: fluxo guiado. `flow.py` (opções e textos), `handler.py` (máquina de estados e transferência), `retomada.py` (retomada por TKT e resumo), `mulher.py` (Ouvidoria da Mulher).
- `backend/app/infrastructure`: SQLite (`sqlite/operational_store.py`), cliente Chatwoot, LLM, embeddings.
- `backend/config`: `units.yaml`, `mulher.yaml`.
- Chaves de recurso (todas desligadas por padrão): `OUVIDORIA_MULHER_ENABLED`, `TICKET_RESUME_ENABLED`, `TICKET_SUMMARY_AI_ENABLED`, `TICKET_FACTS_RETENTION_DAYS=90`.

## Método

Desenvolvimento orientado por especificação (skill `tlc-spec-driven`). Artefatos em `.specs/`:

- `STATE.md`: decisões (AD-NNN) e Handoff. Ler ao retomar o trabalho; atualizar ao decidir algo de projeto e ao pausar.
- `features/<nome>/spec.md` (critérios EARS com IDs), `tasks.md`, `validation.md` (veredito do Verifier).
- Features: `tjpa-atendimento-rag`, `tjpa-roteiro-atendimento`, `ouvidoria-mulher`, `retomada-ticket`.
- Validadores: `validate_spec.py`, `validate_tasks.py`, `validate_state.py` (na pasta `scripts/` da skill). Todo ID de requisito tem uma linha na tabela de rastreabilidade.
- O Verifier é independente do autor; o ciclo corrigir e reverificar vai até 3 rodadas.

## Regras do projeto

- Não remover nem alterar Chatwoot ou dashboard. Não ligar nem desligar o Agent Bot, nem ligá-lo à caixa de entrada, sem pedido explícito. Não criar caixa ou canal novo.
- Não commitar nem fazer push sem pedido. Não executar `chatwoot_setup --apply` sem aprovação (cria também 5 times antigos).
- Nunca registrar CPF, telefone de retorno, relato ou resumo do atendente em log ou auditoria.
- Relatar só resultados de testes reais; separar teste de desenvolvimento de validação; não afirmar homologação sem ensaio real.
- Conteúdo novo do acervo entra como `pending_review` (AD-003). Orquestração determinística; o LLM só gera o texto (AD-002).
- Caso da Ouvidoria é restrito: nunca entra em lista, resumo por IA nem nota de outro time.
- Limites do WhatsApp: título de botão até 20 caracteres, linha de lista até 24, no máximo 10 linhas.

## Implantação (VM)

Contêiner `tjpa_backend`, remoto `/opt/tjpa`, atrás do nginx em `/atendimento/`. Credenciais ficam em `AcessoVM.txt` (ler só na hora de usar, nunca copiar para o repositório).

- Implantar só o código, com backup de `operational.db` e `.env` em `/root/bak-*`.
- Nunca usar `deploy/sync.sh`: sobrescreve o `knowledge.db` da VM.
- Não imprimir tokens ao inspecionar `.env`.
- Manter `docs/TJPA/EscutAI_Pitch.*` intocado.
