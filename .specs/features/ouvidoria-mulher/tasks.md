# Ouvidoria da Mulher Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path.

---

**Design**: inline (sem `design.md`): reutiliza a máquina de estados, o protocolo TKT, a nota de contexto e a transferência confirmada; módulos novos listados em cada tarefa.
**Status**: Implementado e testado offline, sem commits (o responsável não pediu commit). Validação real depende do ensaio com a Ouvidoria.

---

## Test Coverage Matrix

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| Domínio (triagem, contatos) | unit | enumerações, leitura defensiva, limites | `backend/tests/unit/test_triage_mulher.py` | `cd backend && .venv/bin/python -m pytest tests/unit -q` |
| Aplicação e infraestrutura (fluxo, handler, SQLite, Chatwoot) | integration | cada AC OVM-01 a OVM-26 | `backend/tests/integration/test_ouvidoria_mulher.py`, `test_chatwoot_client_and_setup.py` | `cd backend && .venv/bin/python -m pytest -q` |

## Gate Check Commands

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | tarefas de domínio | `cd backend && .venv/bin/python -m pytest tests/unit -q` |
| Full | tarefas com integração | `cd backend && .venv/bin/python -m pytest -q` |

---

## Execution Plan

### Phase 1: Ouvidoria da Mulher

```
T1 → T2 → T3 → T4 → T5
```

---

## Task Breakdown

### T1: Domínio da triagem e contatos

**What**: enumerações fechadas, `Triage` com leitura defensiva, `OuvidoriaMulher`, detecção de relato de violência e carga de `mulher.yaml`.
**Where**: `backend/app/domain/triage_mulher.py`, `backend/app/infrastructure/mulher_file.py`, `backend/config/mulher.yaml`
**Depends on**: None
**Reuses**: padrão de `units_file.py`
**Requirement**: OVM-08, OVM-09, OVM-12, OVM-15, OVM-25

**Done when**:

- [x] Valor fora da enumeração é descartado ao ler e o relato volta byte a byte
- [x] Contato inválido é descartado; arquivo ausente vira cadastro vazio

**Tests**: unit
**Gate**: quick

---

### T2: Estado persistente

**What**: coluna `triage` com migração, limpeza no encerramento, chave e equipe em configuração.
**Where**: `backend/app/infrastructure/sqlite/operational_store.py`, `backend/app/config.py`, `backend/app/container.py`
**Depends on**: T1
**Reuses**: migração por `ALTER TABLE`
**Requirement**: OVM-24, OVM-25

**Done when**:

- [x] Banco anterior abre sem perder dados
- [x] `clear_request` apaga a triagem

**Tests**: unit
**Gate**: quick

---

### T3: Opções, textos e registro no fluxo

**What**: opções e textos da Ouvidoria (com página da Cartilha), menu com a linha 4 sob a chave, tipos de ação.
**Where**: `backend/app/application/chat/mulher.py`, `backend/app/application/chat/flow.py`
**Depends on**: T2
**Reuses**: `_register`, `clean`
**Requirement**: OVM-01, OVM-02, OVM-04, OVM-05, OVM-06, OVM-07, OVM-09

**Done when**:

- [x] Todos os títulos cabem nos limites do WhatsApp e são únicos
- [x] Com a chave desligada o menu é idêntico ao anterior

**Tests**: unit, integration
**Gate**: full

---

### T4: Fluxo no handler

**What**: acolhimento, orientações, triagem por etapas, perigo, contato inseguro sem retorno, saída rápida, oferta por texto livre, encaminhamento com nota, protocolo e horário.
**Where**: `backend/app/application/chat/handler.py`
**Depends on**: T3
**Reuses**: `_do_handoff`, `_attempt_handoff`, `_context_note`, `_channel_recovery`
**Requirement**: OVM-03, OVM-08, OVM-10, OVM-11, OVM-12, OVM-13, OVM-14, OVM-16, OVM-17, OVM-18, OVM-19, OVM-20, OVM-21, OVM-22, OVM-23

**Done when**:

- [x] Nota gravada antes da equipe e aviso só após a confirmação
- [x] Relato e número do processo fora de log e auditoria

**Tests**: integration
**Gate**: full

---

### T5: Chatwoot: cliente e preparação

**What**: atributos de conversa, definições de atributos, time e rótulos no script de preparação (plano por padrão).
**Where**: `backend/app/infrastructure/chatwoot/client.py`, `backend/app/interfaces/chatwoot_setup.py`
**Depends on**: T4
**Reuses**: padrão do `chatwoot_setup`
**Requirement**: OVM-26

**Done when**:

- [x] Plano lista só o que falta e o time existente não é recriado
- [x] Versão sem API de atributos é declarada e o resto segue

**Tests**: integration
**Gate**: full
