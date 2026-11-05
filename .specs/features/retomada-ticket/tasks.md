# Retomada por protocolo Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path.

---

**Design**: inline (sem `design.md`): reutiliza o protocolo TKT, a nota de contexto e a transferência confirmada; módulos novos listados em cada tarefa.
**Status**: Implementado e testado offline, sem commits (o responsável não pediu commit). Validação real depende de ensaio.

---

## Test Coverage Matrix

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| Armazenamento e leitura defensiva | integration | migração, retenção, visibilidade | `backend/tests/integration/test_retomada_ticket.py` | `cd backend && .venv/bin/python -m pytest -q` |
| Fluxo, notas e IA | integration | cada AC RTK-01 a RTK-16 | `backend/tests/integration/test_retomada_ticket.py` | idem |

## Gate Check Commands

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | tarefas de armazenamento | `cd backend && .venv/bin/python -m pytest tests/integration/test_retomada_ticket.py -q` |
| Full | tarefas com fluxo | `cd backend && .venv/bin/python -m pytest -q` |

---

## Execution Plan

### Phase 1: Retomada e resumo

```
T1 → T2 → T3 → T4 → T5 → T6
```

---

## Task Breakdown

### T1: Armazenamento do protocolo

**What**: colunas `contact_id`, `parent_ticket_id`, `team`, `visibility`, `facts`; consultas, ligação única, retenção; API sem dados pessoais.
**Where**: `backend/app/infrastructure/sqlite/operational_store.py`
**Depends on**: None
**Reuses**: migração por `ALTER TABLE`
**Requirement**: RTK-02, RTK-04, RTK-05, RTK-06, RTK-14, RTK-15

**Done when**:

- [x] Banco antigo abre e protocolo antigo não retoma
- [x] Fatos gravados uma vez e só com dono e visibilidade normal

**Tests**: integration
**Gate**: quick

---

### T2: Chaves e configuração

**What**: `TICKET_RESUME_ENABLED`, `TICKET_SUMMARY_AI_ENABLED`, retenção; ligação no contêiner.
**Where**: `backend/app/config.py`
**Depends on**: T1
**Reuses**: padrão das chaves da Ouvidoria
**Requirement**: RTK-03

**Done when**:

- [x] Com as chaves desligadas a suíte anterior passa sem alteração

**Tests**: integration
**Gate**: full

---

### T3: Opções e textos da retomada

**What**: opções, textos, leitura do protocolo, fatos fechados, linhas de histórico e notas.
**Where**: `backend/app/application/chat/retomada.py`
**Depends on**: T2
**Reuses**: `flow._register`, `valid_br_phone`
**Requirement**: RTK-01

**Done when**:

- [x] Títulos dentro dos limites do WhatsApp

**Tests**: integration
**Gate**: full

---

### T4: Fluxo no handler

**What**: retomada pelo menu e digitada, lista, caso restrito, telefone de retorno e Balcão, nota com histórico, fatos no encerramento, `/encerrar` com resumo.
**Where**: `backend/app/application/chat/handler.py`
**Depends on**: T3
**Reuses**: `_expect`, `_do_handoff`, `_attempt_handoff`, `_context_note`
**Requirement**: RTK-02, RTK-03, RTK-04, RTK-07, RTK-08, RTK-09, RTK-10, RTK-11, RTK-16, RTK-17

**Done when**:

- [x] Respostas idênticas para protocolo inexistente, de outro contato, sem dono e sem chat
- [x] Erro no histórico não impede a transferência

**Tests**: integration
**Gate**: full

---

### T5: Resumo por IA

**What**: leitura das mensagens, mascaramento, saída fechada, conferência de números e datas, nota rotulada em segundo plano.
**Where**: `backend/app/application/chat/retomada.py`
**Depends on**: T4
**Reuses**: `mask_pii`, `LlmGenerator`
**Requirement**: RTK-12, RTK-13, RTK-17

**Done when**:

- [x] Número inventado é descartado e falha da IA não afeta a transferência

**Tests**: integration
**Gate**: full

---

### T6: Contexto do processo após a resposta

**What**: depois de responder, com processo localizado, oferecer itens do processo, atendente e encerrar (`PROCESS_AFTER_TEXT` + `process_menu`); sem processo, manter menu, atendente e encerrar.
**Where**: `backend/app/application/chat/handler.py` (`_answer`)
**Depends on**: T5
**Reuses**: `flow.process_menu`
**Requirement**: RTK-18

**Done when**:

- [x] Com processo localizado, as opções são as de `process_menu` e o texto cita o número
- [x] Sem processo localizado, as opções seguem `menu, atendente, encerrar`
- [x] O processo permanece em perguntas seguidas

**Tests**: integration
**Gate**: full
